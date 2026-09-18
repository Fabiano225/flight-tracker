from datetime import timedelta
import json
import uuid

from .alerts import is_drop, reason_for, digest, diverse_take
from .config import cents
from .network import ServiceError, BudgetError
from .planner import plan
from .store import stamp


def scan(config, store, provider, now, demo=False):
    scope = config.scope("demo" if demo else "live")
    run_id = uuid.uuid4().hex
    batches = plan(config, now.date())
    summary = {"run_id": run_id, "started": stamp(now), "mode": "demo" if demo else "live",
        "status": "running", "calendar_queries_planned": len(batches), "calendar_queries_ok": 0,
        "calendar_slots": sum(len(b.pairs()) for b in batches), "calendar_prices": 0,
        "calendar_unknown": 0, "verification_searches": 0, "verified_quotes": 0,
        "queued_deals": 0, "http_attempts": 0, "errors": [], "config": config.public_dict()}
    db = store.db
    db.execute("INSERT INTO runs VALUES(?,?,?,?,?)", (run_id, stamp(now), scope, "running", json.dumps(summary)))
    store.expire(now, config.pending_ttl_hours, scope)
    db.commit()
    candidates = []
    consecutive_errors = 0
    try:
        for batch in batches:
            try:
                rows = provider.fetch(batch)
                summary["calendar_queries_ok"] += 1
                consecutive_errors = 0
            except ServiceError as exc:
                summary["errors"].append(f"{batch.origin}/{batch.profile}/{batch.duration}d: {exc}")
                consecutive_errors += 1
                if isinstance(exc, BudgetError) or consecutive_errors >= 3:
                    summary["errors"].append("Circuit breaker: stopped after repeated errors or exhausted budget")
                    break
                continue
            for (dep, ret), price in rows.items():
                db.execute("INSERT INTO calendar VALUES(?,?,?,?,?,?,?,?)",
                           (run_id, scope, stamp(now), batch.origin, dep, ret, batch.profile, price))
                if price is not None:
                    summary["calendar_prices"] += 1
                    previous = store.previous_low("calendar", scope, batch.origin, dep, ret, batch.profile,
                                                  now, config.history_window_days, run_id)
                    # Drops get priority, then lowest prices within each route/profile.
                    candidates.append((not is_drop(price, previous, config), price, batch.origin, dep, ret, batch.profile))
                else:
                    summary["calendar_unknown"] += 1
            db.commit()  # Preserve completed chunks if a later search fails.
            if not demo:
                print(f"Date batches {summary['calendar_queries_ok']}/{len(batches)}; "
                      f"prices {summary['calendar_prices']}; HTTP {provider.http.used}", flush=True)
        candidates.sort()
        # Check existing alert dates too, so a further meaningful drop is not starved
        # by previously alerted cheap dates. Unalerted dates then rotate naturally.
        def priority(item):
            _, price, origin, dep, ret, profile = item
            category = "nonstop" if profile == "nonstop" else "layover"
            prior = db.execute("""SELECT MIN(a.price) FROM alert_items a JOIN outbox o ON a.outbox_id=o.id
                WHERE a.scope=? AND a.origin=? AND a.departure=? AND a.return_date=? AND a.category=?
                AND o.status IN ('sent','pending')""", (scope, origin, dep, ret, category)).fetchone()[0]
            suppressed = prior is not None and price > prior - cents(config.realert_improvement_eur)
            return (suppressed, *item)
        candidates.sort(key=priority)
        selected = diverse_take(candidates, config.max_verifications_per_run, lambda x: (x[2], x[5]))
        verified = {}
        verification_errors = 0
        for _, _, origin, dep, ret, profile in selected:
            summary["verification_searches"] += 1
            try:
                quotes = provider.verify(origin, dep, ret, profile)
                verification_errors = 0
            except ServiceError as exc:
                summary["errors"].append(f"Verify {origin}/{dep}/{ret}/{profile}: {exc}")
                verification_errors += 1
                if isinstance(exc, BudgetError) or verification_errors >= 3:
                    break
                continue
            for quote in quotes:
                key = (quote.origin, quote.departure, quote.return_date, quote.category)
                if key not in verified or quote.price < verified[key].price:
                    verified[key] = quote
        eligible = []
        for quote in verified.values():
            baseline = store.previous_low("quotes", scope, quote.origin, quote.departure, quote.return_date,
                                           quote.category, now, config.history_window_days, run_id)
            reason = reason_for(quote.price, baseline, quote.category, config)
            prior_alert = store.alerted_low(scope, quote)
            if reason and (prior_alert is None or quote.price <= prior_alert - cents(config.realert_improvement_eur)):
                eligible.append((quote, reason))
            db.execute("INSERT INTO quotes VALUES(?,?,?,?,?,?,?,?,?)", (run_id, scope, stamp(now), quote.origin,
                quote.departure, quote.return_date, quote.category, quote.price, json.dumps(quote.to_dict())))
        summary["verified_quotes"] = len(verified)
        eligible.sort(key=lambda item: item[0].price)
        chosen = diverse_take(eligible, config.max_deals_per_run, lambda item: (item[0].origin, item[0].category))
        if chosen:
            store.enqueue(run_id, "deal", now, digest(chosen, config, now, demo), [q for q, _ in chosen], scope)
            summary["queued_deals"] = len(chosen)
        if batches and not summary["calendar_prices"]:
            summary["errors"].append("No calendar prices received; source health needs attention")
        if batches and summary["calendar_prices"] and not verified:
            summary["errors"].append("No shortlisted itinerary passed round-trip, duration and currency checks")
        summary["status"] = "expired" if not batches else "partial" if summary["errors"] else "ok"
        if summary["status"] == "partial":
            last = store.get_meta("health_alert_at")
            if last is None or last < stamp(now - timedelta(hours=24)):
                store.enqueue(run_id, "health", now,
                    "Flight tracker needs attention.\n" + "\n".join(summary["errors"][:4]) +
                    "\nNo missing or unverified prices are sent as deals. Check the GitHub Actions run.")
                store.set_meta("health_alert_at", stamp(now))
            store.set_meta("unhealthy", "yes")
        elif summary["status"] == "ok" and store.get_meta("unhealthy") == "yes":
            store.enqueue(run_id, "health", now, "Flight tracker recovered: calendar searches and itinerary checks succeeded.")
            store.set_meta("unhealthy", "no")
        summary["http_attempts"] = provider.http.used
        db.execute("UPDATE runs SET status=?,summary=? WHERE id=?", (summary["status"], json.dumps(summary), run_id))
        db.commit()
        return summary
    finally:
        provider.close()

from datetime import timedelta
import json
import uuid

from .alerts import is_drop, diverse_take
from .network import ServiceError, BudgetError
from .planner import plan
from .store import stamp
from .trends import load_watches, watch_searches, queue_trends


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
    store.expire_outside_search(config)
    # Retire unsent legacy rotating overviews after switching notification policy.
    db.execute("UPDATE outbox SET status='expired' WHERE kind='deal' AND status='pending'")
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
        # Recheck stable watched dates first, including price rises. Fill the
        # remaining budget with cheap/drop candidates, not rotating unalerted dates.
        selected = watch_searches(load_watches(store, config, scope, now))[:config.max_verifications_per_run]
        ranked = diverse_take(candidates, len(candidates), lambda x: (x[2], x[5]))
        for _, _, origin, dep, ret, profile in ranked:
            item = (origin, dep, ret, profile)
            if len(selected) >= config.max_verifications_per_run:
                break
            if item not in selected:
                selected.append(item)
        verified = {}
        verification_errors = 0
        for origin, dep, ret, profile in selected:
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
        for quote in verified.values():
            db.execute("INSERT INTO quotes VALUES(?,?,?,?,?,?,?,?,?)", (run_id, scope, stamp(now), quote.origin,
                quote.departure, quote.return_date, quote.category, quote.price, json.dumps(quote.to_dict())))
        summary["verified_quotes"] = len(verified)
        summary["queued_deals"] = queue_trends(store, config, scope, run_id, verified, now, demo)
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

from datetime import timedelta
import hashlib
import json
import uuid

from .alerts import is_drop, diverse_take
from .network import ServiceError, BudgetError
from .planner import plan
from .store import stamp
from .check_status import queue_check_status
from .notifications import SETTINGS
from .trends import load_watches, watch_searches, queue_trends

# A route (airport + nonstop/any profile) whose complete date search found no
# price at all is searched again only about once a day, until it has prices.
EMPTY_ROUTE_RECHECK = timedelta(hours=23)


def route_key(scope, origin, profile):
    return f"empty_route:{scope}:{origin}:{profile}"


def search_window(config):
    return [config.departure_start, config.departure_end, config.min_trip_days, config.max_trip_days]


def queue_window_ended(store, config, run_id, now):
    """Say once per search window that its last departure date has been reached."""
    window = json.dumps([config.destination, list(config.origins), *search_window(config)])
    key = "window_ended:" + hashlib.sha256(window.encode()).hexdigest()[:16]
    if store.get_meta(key):
        return False
    store.enqueue(run_id, "notice", now, f"{config.destination} search window ended\n{stamp(now)}\n"
                  f"Departures from {config.departure_start} to {config.departure_end} can no longer be searched, "
                  f"so the tracker has stopped searching this trip. Stored prices stay on the dashboard.\n"
                  f"Set up a new search: {SETTINGS}")
    store.set_meta(key, stamp(now))
    return True


def quiet_route(store, config, scope, origin, profile, now):
    value = store.get_meta(route_key(scope, origin, profile))
    try:
        data = json.loads(value) if value else None
        return bool(data) and data["window"] == search_window(config) and data["at"] > stamp(now - EMPTY_ROUTE_RECHECK)
    except (ValueError, TypeError, KeyError):
        return False


def scan(config, store, provider, now, demo=False):
    scope = config.scope("demo" if demo else "live")
    run_id = uuid.uuid4().hex
    planned = plan(config, now.date())
    batches = [b for b in planned if not quiet_route(store, config, scope, b.origin, b.profile, now)]
    summary = {"run_id": run_id, "started": stamp(now), "mode": "demo" if demo else "live",
        "status": "running", "calendar_queries_planned": len(batches), "calendar_queries_ok": 0,
        "calendar_queries_skipped": len(planned) - len(batches),
        "calendar_slots": sum(len(b.pairs()) for b in batches), "calendar_prices": 0,
        "calendar_unknown": 0, "verification_searches": 0, "verified_quotes": 0,
        "queued_deals": 0, "http_attempts": 0, "errors": [], "config": config.public_dict()}
    db = store.db
    db.execute("INSERT INTO runs VALUES(?,?,?,?,?)", (run_id, stamp(now), scope, "running", json.dumps(summary)))
    store.expire(now, config.pending_ttl_hours, scope)
    store.expire_outside_search(config)
    # Retire unsent legacy rotating overviews after switching notification policy.
    db.execute("UPDATE outbox SET status='expired' WHERE kind='deal' AND status='pending'")
    db.execute("UPDATE outbox SET status='expired' WHERE kind='check_status' AND status='pending'")
    db.commit()
    candidates = []
    consecutive_errors = 0
    route_total, route_ok, route_prices = {}, {}, {}
    for batch in batches:
        route = (batch.origin, batch.profile)
        route_total[route] = route_total.get(route, 0) + 1
        route_ok.setdefault(route, 0)
        route_prices.setdefault(route, 0)
    try:
        for batch in batches:
            try:
                rows = provider.fetch(batch)
                summary["calendar_queries_ok"] += 1
                route_ok[(batch.origin, batch.profile)] += 1
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
                    route_prices[(batch.origin, batch.profile)] += 1
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
        for (origin, profile), total in route_total.items():
            key = route_key(scope, origin, profile)
            if route_prices[(origin, profile)]:
                db.execute("DELETE FROM meta WHERE key=?", (key,))
            elif route_ok[(origin, profile)] == total:
                # Only a complete, error-free search may mark a route as empty.
                store.set_meta(key, json.dumps({"at": stamp(now), "window": search_window(config)}))
        db.commit()
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
        save_cache = getattr(provider, "save_search_cache", None)
        if save_cache:
            try:
                save_cache(run_id)
            except Exception:
                pass  # Optional speed-up only; the baggage step then searches live.
        summary["queued_deals"] = queue_trends(store, config, scope, run_id, verified, now, demo)
        if batches and not summary["calendar_prices"]:
            summary["errors"].append("No calendar prices received; source health needs attention")
        if batches and summary["calendar_prices"] and not verified:
            summary["errors"].append("No shortlisted itinerary passed round-trip, duration and currency checks")
        summary["status"] = "expired" if not planned else "partial" if summary["errors"] else "ok"
        summary['queued_check_status'] = int(queue_check_status(
            store, config, scope, run_id, verified, now, summary, demo))
        if summary["status"] == "expired" and not demo:
            summary["queued_window_notice"] = int(queue_window_ended(store, config, run_id, now))
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
        summary["calendar_dates_recovered"] = getattr(getattr(provider, "dates", None), "recovered_dates", 0)
        summary["pruned_rows"] = store.prune(now, config.history_window_days)
        db.execute("UPDATE runs SET status=?,summary=? WHERE id=?", (summary["status"], json.dumps(summary), run_id))
        db.commit()
        return summary
    finally:
        provider.close()

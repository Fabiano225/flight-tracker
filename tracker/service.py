from dataclasses import replace
from datetime import date, timedelta
import hashlib
import json
import sqlite3
import uuid

from .alerts import is_drop, diverse_take
from .config import MAIN_TRIP
from .network import ServiceError, BudgetError
from .planner import plan, days
from .store import stamp
from .check_status import queue_check_status
from .notifications import SETTINGS
from .trends import load_watches, watch_searches, queue_trends

# A route (airport + nonstop/any profile) whose complete date search found no
# price at all is searched again only about once a day, until it has prices.
EMPTY_ROUTE_RECHECK = timedelta(hours=23)
# An airport whose searches fail this many times in a row is left out for the rest of
# the run; the other airports continue after a short pause.
AIRPORT_FAILURES = 3
AIRPORT_PAUSE_SECONDS = 60


def gaps_key(scope):
    return f"calendar_gaps:{scope}"


def load_gaps(store, config, scope):
    """Date searches of the last run without an answer, as (origin, profile, days, departure);
    none once the search window changed."""
    try:
        data = json.loads(store.get_meta(gaps_key(scope)) or "null")
        if not data or data["window"] != search_window(config):
            return set()
        return {(origin, profile, int(length), dep) for origin, profile, length, dep in data["cells"]}
    except (ValueError, TypeError, KeyError):
        return set()


def save_gaps(store, config, scope, gaps):
    if gaps:
        store.set_meta(gaps_key(scope), json.dumps({"window": search_window(config), "cells": sorted(gaps)}))
    else:
        store.db.execute("DELETE FROM meta WHERE key=?", (gaps_key(scope),))


def split_gaps(batches, gaps):
    """Catch-up batches with the last run's unanswered dates, to search first, and the
    regular batches without those dates: every date is still asked once per run."""
    wanted = {}
    for origin, profile, length, dep in gaps:
        wanted.setdefault((origin, profile, length), set()).add(date.fromisoformat(dep))
    first, rest = [], []
    for batch in batches:
        dates = {d for d in wanted.get((batch.origin, batch.profile, batch.duration), ()) if batch.start <= d <= batch.end}
        if not dates:
            rest.append(batch)
            continue
        first.append(replace(batch, only=tuple(sorted(dates))))
        remaining = tuple(d for d in days(batch.start, batch.end) if d not in dates)
        if remaining:
            rest.append(replace(batch, only=remaining))
    return first, rest


def cells(batch, departures=None):
    """(origin, profile, days, departure) of a batch's dates, or of the given departures."""
    return {(batch.origin, batch.profile, batch.duration, dep)
            for dep in (departures if departures is not None else [dep for dep, _ in batch.pairs()])}


def spread_checks(selected, ranked, limit):
    """Fill the flight checks after the watched dates with the ranked candidates, at most
    an even share per departure day first, so the checks cover more days; checks left
    over then go to the next candidates in order, wherever they leave."""
    selected = list(selected)
    departures = {dep for _, dep, _, _ in ranked}
    share = max(2, -(-limit // max(1, len(departures))))
    per_day = {}
    for _, dep, _, _ in selected:
        per_day[dep] = per_day.get(dep, 0) + 1
    for capped in (True, False):
        for item in ranked:
            if len(selected) >= limit:
                return selected
            if item in selected or (capped and per_day.get(item[1], 0) >= share):
                continue
            selected.append(item)
            per_day[item[1]] = per_day.get(item[1], 0) + 1
    return selected


def route_key(scope, origin, profile):
    return f"empty_route:{scope}:{origin}:{profile}"


def search_window(config):
    window = [config.departure_start, config.departure_end, config.min_trip_days, config.max_trip_days]
    # Appended only when set, so existing searches keep their stored keys.
    return window + [config.latest_return] if config.latest_return else window


def queue_window_ended(store, config, run_id, now, trips=None):
    """Say once per search window that its last departure date has been reached.
    A trip with several destinations says it once, from its first destination's search."""
    siblings = [trip.destination for trip in (trips or (config,)) if trip.id == config.id] or [config.destination]
    if config.destination != siblings[0]:
        return False
    trip = [] if config.id == MAIN_TRIP else [config.id]
    places = config.destination if len(siblings) == 1 else siblings
    window = json.dumps([places, list(config.origins), *search_window(config), *trip])
    key = "window_ended:" + hashlib.sha256(window.encode()).hexdigest()[:16]
    if store.get_meta(key):
        return False
    to = f" to {', '.join(siblings)}" if len(siblings) > 1 else ""
    store.enqueue(run_id, "notice", now, f"{config.destination} search window ended\n{stamp(now)}\n"
                  f"Departures from {config.departure_start} to {config.departure_end}{to} can no longer be searched, "
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


def scan_trips(trips, store, providers, now, demo=False, everything=None):
    """Search every trip in turn. `providers(trip)` returns the trip's provider;
    live providers share one paced client and thereby one request budget.
    `everything`: all searches, when only some of them run now."""
    summaries = [scan_trip(config, store, providers(config), now, demo, everything or trips) for config in trips]
    update_health(store, trips, summaries, now)
    try:
        # The longest comparison period of all trips, searched now or not, decides
        # what every trip may still read.
        pruned = store.prune(now, max(config.history_window_days for config in everything or trips))
    except sqlite3.Error as exc:
        # Cleanup is housekeeping; a failure must not lose this run's results.
        pruned = None
        summaries[-1]["prune_error"] = str(exc)[:200]
    if pruned is not None:
        summaries[-1]["pruned_rows"] = pruned
    last = summaries[-1]
    store.db.execute("UPDATE runs SET summary=? WHERE id=?", (json.dumps(last), last["run_id"]))
    store.db.commit()
    return summaries


def incomplete(summary):
    """Whether a search missed a real part of its prices: none at all, no checked flight,
    or more than a quarter (and more than one) of its date searches or flight checks.
    Single gaps are not; they stay visible in the run's errors."""
    if not summary["calendar_queries_planned"]:
        return False
    big = lambda missing, total: missing >= 2 and missing * 4 > total
    checks = summary.get("verification_planned", 0)
    return (not summary["calendar_prices"]
            or (checks > 0 and not summary["verified_quotes"])
            or big(summary["calendar_slots"] - summary.get("calendar_answered", summary["calendar_slots"]),
                   summary["calendar_slots"])
            or big(checks - summary["verification_searches"] + summary.get("verification_failed", 0), checks))


def update_health(store, trips, summaries, now):
    """One health message per run for all trips; the flight source is shared. Only
    incomplete searches count; single gaps send no message."""
    several = len(summaries) > 1
    partial = [(config, summary) for config, summary in zip(trips, summaries) if summary.get("incomplete")]
    if partial:
        last = store.get_meta("health_alert_at")
        if last is None or last < stamp(now - timedelta(hours=24)):
            errors = [f"{config.destination}: {error}" if several else error
                      for config, summary in partial for error in summary["errors"][:2 if several else 4]]
            store.enqueue(partial[0][1]["run_id"], "health", now,
                "Flight tracker needs attention.\n" + "\n".join(errors[:10]) +
                "\nNo missing or unverified prices are sent as deals. Check the GitHub Actions run.")
            store.set_meta("health_alert_at", stamp(now))
        store.set_meta("unhealthy", "yes")
    else:
        ok = [summary for summary in summaries if summary["status"] != "expired"]
        if ok and store.get_meta("unhealthy") == "yes":
            store.enqueue(ok[0]["run_id"], "health", now,
                          "Flight tracker recovered: calendar searches and itinerary checks succeeded.")
            store.set_meta("unhealthy", "no")
    store.db.commit()


class Pause:
    """A pause before the next request once an airport has been left out, so a struggling
    source gets a moment; none when nothing follows. Demo providers do not wait."""
    def __init__(self, provider):
        self.sleep = getattr(provider.http, "sleep", None)
        self.due = False

    def request(self):
        self.due = True

    def before_next(self):
        if self.due and self.sleep:
            self.sleep(AIRPORT_PAUSE_SECONDS)
        self.due = False


def scan_trip(config, store, provider, now, demo=False, trips=None):
    """Search one trip. `trips` are all current trips, searched now or not; alerts of others expire."""
    mode = "demo" if demo else "live"
    scope = config.scope(mode)
    run_id = uuid.uuid4().hex
    planned = plan(config, now.date())
    batches = [b for b in planned if not quiet_route(store, config, scope, b.origin, b.profile, now)]
    # The last run's dates without an answer are asked first; the regular batches leave
    # them out. Dates without an answer now wait for the next run instead of being
    # asked again in this one.
    catch_up, regular = split_gaps(batches, load_gaps(store, config, scope))
    batches = catch_up + regular
    catching = set(catch_up)
    gaps = set()
    summary = {"run_id": run_id, "started": stamp(now), "mode": mode, "trip": config.id,
        "status": "running", "calendar_queries_planned": len(batches), "calendar_queries_ok": 0,
        "calendar_queries_skipped": len(planned) - len(batches),
        "calendar_slots": sum(len(b.pairs()) for b in batches), "calendar_prices": 0,
        "calendar_unknown": 0, "calendar_answered": 0, "verification_planned": 0, "verification_searches": 0,
        "verification_failed": 0, "verified_quotes": 0,
        "queued_deals": 0, "http_attempts": 0, "errors": [], "config": config.public_dict()}
    used_before = provider.http.used
    db = store.db
    db.execute("INSERT INTO runs VALUES(?,?,?,?,?)", (run_id, stamp(now), scope, "running", json.dumps(summary)))
    trips = trips or (config,)
    store.expire(now, config.pending_ttl_hours, {trip.scope(mode) for trip in trips})
    store.expire_outside_search(trips)
    store.expire_check_status(scope)
    db.commit()
    candidates = []
    # Failures in a row per departure airport; a flaky airport must not stop the others.
    failures, stopped = {}, set()
    pause = Pause(provider)
    route_total, route_ok, route_prices = {}, {}, {}
    for batch in batches:
        route = (batch.origin, batch.profile)
        route_total[route] = route_total.get(route, 0) + 1
        route_ok.setdefault(route, 0)
        route_prices.setdefault(route, 0)
    recovered = 0
    try:
        for index, batch in enumerate(batches):
            if batch.origin in stopped:
                gaps |= cells(batch)
                continue
            pause.before_next()
            try:
                rows = provider.fetch(batch)
                summary["calendar_queries_ok"] += 1
                failures[batch.origin] = 0
            except ServiceError as exc:
                summary["errors"].append(f"{batch.origin}/{batch.profile}/{batch.duration}d: {exc}")
                gaps |= cells(batch)
                if isinstance(exc, BudgetError):
                    summary["errors"].append("Circuit breaker: stopped after a source block or exhausted budget")
                    for later in batches[index + 1:]:
                        gaps |= cells(later)
                    break
                failures[batch.origin] = failures.get(batch.origin, 0) + 1
                if failures[batch.origin] >= AIRPORT_FAILURES:
                    stopped.add(batch.origin)
                    summary["errors"].append(f"{batch.origin}: date searches stopped after repeated errors; "
                                             "the other airports continue")
                    pause.request()
                continue
            missed = set(getattr(provider, "missed", ()))
            gaps |= cells(batch, missed)
            if batch in catching:
                recovered += len(rows) - len(missed)
            if missed:
                summary["errors"].append(f"{batch.origin}/{batch.profile}/{batch.duration}d: "
                                         f"{len(missed)} of {len(rows)} dates got no answer")
            else:
                # Only a batch where every date answered may help mark a route as empty.
                route_ok[(batch.origin, batch.profile)] += 1
            summary["calendar_answered"] += len(rows) - len(missed)
            for (dep, ret), price in rows.items():
                if dep in missed:
                    # No answer: not stored, so the dashboard can show the last known price.
                    summary["calendar_unknown"] += 1
                    continue
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
                print(f"{config.destination} date batches {summary['calendar_queries_ok']}/{len(batches)}; "
                      f"prices {summary['calendar_prices']}; HTTP {provider.http.used}", flush=True)
        for (origin, profile), total in route_total.items():
            key = route_key(scope, origin, profile)
            if route_prices[(origin, profile)]:
                db.execute("DELETE FROM meta WHERE key=?", (key,))
            elif route_ok[(origin, profile)] == total:
                # Only a complete, error-free search may mark a route as empty.
                store.set_meta(key, json.dumps({"at": stamp(now), "window": search_window(config)}))
        save_gaps(store, config, scope, gaps)
        db.commit()
        candidates.sort()
        # Recheck stable watched dates first, including price rises. Fill the
        # remaining budget with cheap/drop candidates, not rotating unalerted dates.
        selected = watch_searches(load_watches(store, config, scope, now))[:config.max_verifications_per_run]
        ranked = diverse_take(candidates, len(candidates), lambda x: (x[2], x[5]))
        selected = spread_checks(selected, [(origin, dep, ret, profile) for _, _, origin, dep, ret, profile in ranked],
                                 config.max_verifications_per_run)
        verified = {}
        summary["verification_planned"] = len(selected)
        failures = {}
        for origin, dep, ret, profile in selected:
            if failures.get(origin, 0) >= AIRPORT_FAILURES:
                continue
            pause.before_next()
            summary["verification_searches"] += 1
            try:
                quotes = provider.verify(origin, dep, ret, profile)
                failures[origin] = 0
            except ServiceError as exc:
                summary["errors"].append(f"Verify {origin}/{dep}/{ret}/{profile}: {exc}")
                summary["verification_failed"] += 1
                if isinstance(exc, BudgetError):
                    break
                failures[origin] = failures.get(origin, 0) + 1
                if failures[origin] >= AIRPORT_FAILURES:
                    summary["errors"].append(f"{origin}: flight checks stopped after repeated errors; "
                                             "the other airports continue")
                    pause.request()
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
        summary["incomplete"] = summary["status"] == "partial" and incomplete(summary)
        # Incomplete searches of this destination in a row; the search schedule repeats such a
        # trip sooner, but at most twice before its next regular search.
        before = db.execute("SELECT summary FROM runs WHERE scope=? AND id<>? ORDER BY started DESC, rowid DESC LIMIT 1",
                            (scope, run_id)).fetchone()
        previous = json.loads(before[0]).get("incomplete_in_row", 0) if before else 0
        summary["incomplete_in_row"] = previous + 1 if summary["incomplete"] else 0
        summary['queued_check_status'] = int(queue_check_status(
            store, config, scope, run_id, verified, now, summary, demo))
        if summary["status"] == "expired" and not demo:
            summary["queued_window_notice"] = int(queue_window_ended(store, config, run_id, now, trips))
        summary["http_attempts"] = provider.http.used - used_before
        # Dates the last run got no answer for, answered now by asking them first.
        summary["calendar_dates_recovered"] = recovered
        db.execute("UPDATE runs SET status=?,summary=? WHERE id=?", (summary["status"], json.dumps(summary), run_id))
        db.commit()
        return summary
    finally:
        provider.close()

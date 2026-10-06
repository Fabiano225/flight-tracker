"""Search settings from the website form, stored outside the protected main branch.

`apply` validates the settings in an owner's issue (see the Apply search settings
workflow) and commits them as config.json to the `search-config` branch. `use`
copies that file over the checkout's config.json before a scan or dashboard build.
Until the branch exists, config.json from main applies unchanged; deleting the
branch returns to it.

An issue holds either all trips ({"trips": [...], "primary_trip": ..., shared
request settings}) or a single trip's settings, which replace the primary trip.
"""
import argparse
from dataclasses import fields, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tracker.config import MAX_TRIPS, OPTIONAL, SHARED_FIELDS, TARGET_CATEGORIES, Config, Settings, shared_values, trips_of
from tracker.places import airline_supported, place, supported
from tracker.planner import plan, request_estimate, settings_estimate

BRANCH = "refs/heads/search-config"
MARKER = "<!-- flightwatch-search-settings -->"
MAX_BODY = 20000
LABELS = {
    "origins": "Departure airports", "destination": "Destination", "departure_start": "Earliest departure",
    "departure_end": "Latest departure", "min_trip_days": "Shortest trip (days)",
    "max_trip_days": "Longest trip (days)", "latest_return": "Latest return", "currency": "Currency", "adults": "Travellers",
    "travel_class": "Cabin", "max_direction_minutes": "Max. travel time per direction (minutes)",
    "hide_separate_tickets": "Hide separate tickets", "carry_on_bags": "Cabin bag in the base search",
    "checked_bags": "Checked bag in the base search", "good_deal_nonstop_eur": "Price target, non-stop (€)",
    "good_deal_layover_eur": "Price target, with stops (€)", "origin_targets": "Price targets per airport", "drop_percent": "Strong deal: at least % below the low",
    "drop_eur": "Strong deal: at least € below the low", "history_window_days": "Comparison period (days)",
    "realert_improvement_eur": "New price alert from a change of (€)", "max_deals_per_run": "Max. offers per message",
    "pending_ttl_hours": "Undelivered messages expire after (hours)",
    "max_verifications_per_run": "Flight checks per run", "outbound_candidates": "Outbound candidates per check",
    "max_http_attempts_per_run": "Request budget per run", "max_run_seconds": "Time budget per run (seconds)",
    "http_timeout_seconds": "Timeout per request (seconds)", "http_attempts": "Attempts per request",
    "request_interval_seconds": "Gap between requests (seconds)", "max_parallel_requests": "Parallel requests",
    "airlines": "Only these airlines", "airlines_exclude": "Exclude these airlines",
    "max_stops": "Max. stops per direction", "display_names": "Display names", "id": "Trip ID",
    "primary_trip": "Shown first on the website", "trips": "Trips",
}
CLASSES = {"economy": "Economy", "premium_economy": "Premium Economy", "business": "Business", "first_class": "First"}


class Rejected(ValueError):
    """Settings that must not be applied; the reason is shown on the issue."""


def git(*args, data=None, check=True):
    result = subprocess.run(["git", *args], input=data, capture_output=True)
    if check and result.returncode:
        raise RuntimeError(f"Git {args[0]} failed; check repository access or concurrent changes")
    return result


def stored():
    """Return (commit, config.json text) of the search-config branch, or (None, None)."""
    remote = git("ls-remote", "--exit-code", "--heads", "origin", BRANCH, check=False)
    if remote.returncode == 2:
        return None, None
    if remote.returncode:
        raise RuntimeError("Search settings lookup failed; refusing to guess the active search")
    git("fetch", "--depth=1", "origin", BRANCH)
    commit = git("rev-parse", "FETCH_HEAD").stdout.decode().strip()
    return commit, git("show", f"{commit}:config.json").stdout.decode("utf-8")


def serialize(settings):
    return json.dumps(settings.file_dict(), indent=2, ensure_ascii=False) + "\n"


def route(config, arrow="→"):
    return f"{', '.join(config.origins)} {arrow} {config.destination}"


def routes(settings, arrow="→"):
    return " · ".join(route(trip, arrow) for trip in ordered(settings))


def ordered(settings):
    return settings.ordered() if isinstance(settings, Settings) else (settings,)


def use(path):
    commit, text = stored()
    if commit is None:
        print("Search settings: config.json from main")
        return
    settings = Settings.from_dict(json.loads(text))  # Fail closed rather than search a stale route.
    Path(path).write_text(text, encoding="utf-8")
    print(f"Search settings from the website form ({commit[:7]}): {routes(settings, '->')}")


def parse_issue(body):
    """Return the issue's Settings (all trips) or Config (a single trip's settings)."""
    if not body or len(body) > MAX_BODY or MARKER not in body:
        raise Rejected("This issue contains no search settings from the form.")
    block = re.search(r"```json[ \t]*\r?\n(.*?)\r?\n```", body, re.S)
    if not block:
        raise Rejected("The settings block (```json … ```) is missing from the issue.")
    try:
        values = json.loads(block.group(1))
    except ValueError:
        raise Rejected("The settings block is not valid JSON.") from None
    if not isinstance(values, dict):
        raise Rejected("The settings block must be a JSON object.")
    required = {f.name for f in fields(Config)} - set(OPTIONAL)
    if "trips" in values:
        trips = values["trips"]
        if not isinstance(trips, list) or not 1 <= len(trips) <= MAX_TRIPS:
            raise Rejected(f"Choose 1 to {MAX_TRIPS} trips.")
        missing = sorted(set(SHARED_FIELDS) - set(values))
        for number, trip in enumerate(trips, 1):
            if isinstance(trip, dict):
                missing += [f"trip {number}: {name}" for name in sorted((required | {"id"}) - set(SHARED_FIELDS) - set(trip))]
    else:
        missing = sorted(required - set(values))
    if missing:
        # Defaults would silently fall back to the original Bangkok search.
        raise Rejected("Settings are missing: " + ", ".join(missing))
    try:
        return Settings.from_dict(values) if "trips" in values else Config.from_dict(values)
    except (ValueError, TypeError) as exc:
        raise Rejected(f"Invalid setting: {exc}") from None


def with_primary(current, config):
    """A single trip's settings replace the primary trip; the other trips keep theirs."""
    primary = replace(config, id=current.primary_trip)
    shared = shared_values(primary)
    trips = tuple(primary if trip.id == current.primary_trip else replace(trip, **shared) for trip in current.trips)
    return Settings(trips, current.primary_trip)


def normalize(settings):
    if isinstance(settings, Settings):
        return Settings(tuple(normalize(trip) for trip in settings.trips), settings.primary_trip)
    config = settings
    codes = (*config.origins, config.destination)
    names = {code: name for code, name in config.display_names.items()
             if code in codes and name != place(code)["city"]}
    # An airport's own price target equal to the trip's adds nothing.
    trip = {"nonstop": config.good_deal_nonstop_eur, "layover": config.good_deal_layover_eur}
    # Same order as the form: departure airports as listed, non-stop before with stops.
    targets = {code: {k: config.origin_targets[code][k] for k in TARGET_CATEGORIES
                      if k in config.origin_targets[code] and config.origin_targets[code][k] != trip[k]}
               for code in config.origins if code in config.origin_targets}
    return Config.from_dict({**config.form_dict(), "display_names": names,
                             "origin_targets": {code: own for code, own in targets.items() if own}})


def trip_name(trip, trips):
    """A trip's name in replies: its destination, with the id if two trips share it."""
    same = sum(other.destination == trip.destination for other in trips) > 1
    return f"{trip.destination} ({trip.id})" if same else trip.destination


def check(settings, today):
    trips = trips_of(settings)
    for trip in trips:
        label = "" if len(trips) == 1 else f"{trip_name(trip, trips)} trip: "
        for code in (*trip.origins, trip.destination):
            if not supported(code):
                raise Rejected(f"{label}The airport {code} is not supported by the flight search.")
        for code in (*trip.airlines, *trip.airlines_exclude):
            if not airline_supported(code):
                raise Rejected(f"{label}The airline {code} is not supported by the flight search.")
        if not plan(trip, today):
            raise Rejected(f"{label}The departure window has no day from tomorrow on, so there would be nothing to search."
                           + (" Choose new dates or remove this trip." if label else ""))
    # All trips of a run share one request and time budget.
    estimate = settings_estimate(settings, today) if isinstance(settings, Settings) else request_estimate(settings, today)
    together = " for all trips together" if len(trips) > 1 else ""
    budget = trips[0]
    if estimate["requests"] > budget.max_http_attempts_per_run:
        raise Rejected(f"Too many requests: about {estimate['requests']} per run{together}, budget "
                       f"{budget.max_http_attempts_per_run}. Choose fewer departure days, trip lengths or airports.")
    if estimate["seconds"] > budget.max_run_seconds:
        raise Rejected(f"Search too long: about {estimate['seconds'] // 60} minutes per run{together}, limit "
                       f"{budget.max_run_seconds // 60} minutes. Choose fewer departure days, trip lengths or airports.")
    return estimate


def shown(name, value):
    if name == "latest_return":
        return value or "none"
    if name == "origins":
        return ", ".join(value)
    if name in ("airlines", "airlines_exclude"):
        return ", ".join(value) or ("all" if name == "airlines" else "none")
    if name == "max_stops":
        return "any" if value is None else "non-stop only" if value == 0 else f"up to {value}"
    if name == "display_names":
        return ", ".join(f"{k}: {v}" for k, v in sorted(value.items())) or "automatic"
    if name == "origin_targets":
        words = {"nonstop": "non-stop", "layover": "with stops"}
        return "; ".join(f"{code}: " + ", ".join(f"{words[k]} €{v:g}" for k, v in sorted(targets.items(), key=lambda x: x[0] != "nonstop"))
                         for code, targets in sorted(value.items())) or "same for all airports"
    if name == "travel_class":
        return CLASSES.get(value, value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def cell(text):
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def trip_changes(old, new, prefix="", skip=()):
    old_values, new_values = old.public_dict(), new.public_dict()
    return [(prefix + LABELS[f.name], shown(f.name, old_values[f.name]), shown(f.name, new_values[f.name]))
            for f in fields(Config) if f.name not in skip and old_values[f.name] != new_values[f.name]]


def primary_id(settings):
    return settings.primary_trip if isinstance(settings, Settings) else settings.id


def summary(trip):
    back = f", back by {trip.latest_return}" if trip.latest_return else ""
    return f"{route(trip)}, {trip.departure_start} to {trip.departure_end}, {trip.min_trip_days}–{trip.max_trip_days} days{back}"


def changes(old, new):
    """Rows (setting, before, new) for the issue reply; trips are matched by id."""
    old_trips, new_trips = trips_of(old), trips_of(new)
    if len(old_trips) == len(new_trips) == 1 and old_trips[0].id == new_trips[0].id:
        return trip_changes(old_trips[0], new_trips[0])
    old_by_id = {trip.id: trip for trip in old_trips}
    rows = [(LABELS[name], shown(name, before), shown(name, after)) for name, before, after in
            zip(SHARED_FIELDS, shared_values(old_trips[0]).values(), shared_values(new_trips[0]).values()) if before != after]
    if primary_id(old) != primary_id(new):
        before = old_by_id[primary_id(old)]
        rows.append((LABELS["primary_trip"], trip_name(before, old_trips),
                     trip_name(next(t for t in new_trips if t.id == primary_id(new)), new_trips)))
    for trip in new_trips:
        before = old_by_id.get(trip.id)
        if before is None:
            rows.append((LABELS["trips"], "—", "added: " + summary(trip)))
        else:
            rows += trip_changes(before, trip, trip_name(trip, new_trips) + " · ", SHARED_FIELDS)
    new_ids = {trip.id for trip in new_trips}
    rows += [(LABELS["trips"], summary(trip), "removed") for trip in old_trips if trip.id not in new_ids]
    return rows


def save(text, parent, message):
    blob = git("hash-object", "-w", "--stdin", data=text.encode("utf-8")).stdout.decode().strip()
    tree = git("mktree", data=f"100644 blob {blob}\tconfig.json\n".encode()).stdout.decode().strip()
    args = ["-c", "user.name=github-actions[bot]", "-c",
            "user.email=41898282+github-actions[bot]@users.noreply.github.com", "commit-tree", tree]
    if parent:
        args.extend(["-p", parent])
    commit = git(*args, data=message.encode("utf-8")).stdout.decode().strip()
    git("push", "origin", f"{commit}:{BRANCH}")  # Fast-forward only; a concurrent change fails closed.
    return commit


def apply(body, path, today, issue=None):
    """Return (outcome, reply markdown). Outcomes: applied, unchanged, rejected."""
    parent, text = stored()
    current = Settings.from_dict(json.loads(text)) if parent else Settings.load(path)
    try:
        new = parse_issue(body)
        if isinstance(new, Config):
            new = with_primary(current, new)
        new = normalize(new)
        estimate = check(new, today)
    except (ValueError, TypeError) as exc:
        reason = exc if isinstance(exc, Rejected) else f"Invalid setting: {exc}"
        return "rejected", (f"❌ **Not applied:** {reason}\n\n"
                            "The current search keeps running unchanged. Please correct it in the form and send it again.")
    diff = changes(current, new)
    if not diff:
        return "unchanged", "ℹ️ **No change:** These settings already match the current search."
    several = len(new.trips) > 1
    commit = save(serialize(new), parent, f"Search settings{f' from issue #{issue}' if issue else ''}: {routes(new)}\n")
    lines = [("✅ **New searches applied:** " if several else "✅ **New search applied:** ") + routes(new), "",
             "| Setting | Before | New |", "|---|---|---|"]
    lines += [f"| {cell(label)} | {cell(old)} | {cell(value)} |" for label, old, value in diff]
    lines.append("")
    old_scopes = {trip.id: trip.scope() for trip in current.trips}
    restarted = [trip for trip in new.trips if old_scopes.get(trip.id, trip.scope()) != trip.scope()]
    if restarted and not several and len(current.trips) == 1:
        lines += ["ℹ️ Settings that define comparable prices changed (destination, cabin, bags, separate "
                  "tickets, travel time limit, airlines or stops): price history and alerts start over for this "
                  "search. The old history stays stored.", ""]
    elif restarted:
        names = ", ".join(trip_name(trip, new.trips) for trip in restarted)
        lines += [f"ℹ️ Settings that define comparable prices changed for {names} (destination, cabin, bags, "
                  "separate tickets, travel time limit, airlines or stops): price history and alerts start over "
                  f"for {'this trip' if len(restarted) == 1 else 'these trips'}. The old history stays stored.", ""]
    if several:
        lines += [f"All {len(new.trips)} trips are searched in every run and share its budget of "
                  f"{new.trips[0].max_http_attempts_per_run} requests. Messages for all trips go to the same "
                  "channel; each names its destination.", ""]
    lines += [f"The website shows the new {'searches' if several else 'search'} in about 2 minutes. A search run "
              f"has started (about {estimate['requests']} requests); first prices appear after about 10–40 minutes.", "",
              f"Stored on the `search-config` branch (commit {commit[:7]}). `config.json` on main stays "
              "unchanged and applies again only if you delete that branch."]
    return "applied", "\n".join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["use", "apply"])
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--reply", help="apply: write the issue reply (Markdown) to this file")
    args = parser.parse_args()
    if args.command == "use":
        use(args.config)
        return
    outcome, reply = apply(os.environ.get("ISSUE_BODY", ""), args.config, datetime.now(timezone.utc).date(),
                           os.environ.get("ISSUE_NUMBER"))
    if args.reply:
        Path(args.reply).write_text(reply + "\n", encoding="utf-8")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"outcome={outcome}\n")
    print(f"Search settings: {outcome}")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"Search settings error: {exc}", file=sys.stderr)
        sys.exit(1)

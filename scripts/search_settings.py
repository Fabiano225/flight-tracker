"""Search settings from the website form, stored outside the protected main branch.

`apply` validates the settings in an owner's issue (see the Apply search settings
workflow) and commits them as config.json to the `search-config` branch. `use`
copies that file over the checkout's config.json before a scan or dashboard build.
Until the branch exists, config.json from main applies unchanged; deleting the
branch returns to it.
"""
import argparse
from dataclasses import fields
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tracker.config import OPTIONAL, Config
from tracker.places import airline_supported, place, supported
from tracker.planner import plan, request_estimate

BRANCH = "refs/heads/search-config"
MARKER = "<!-- flightwatch-search-settings -->"
MAX_BODY = 20000
LABELS = {
    "origins": "Departure airports", "destination": "Destination", "departure_start": "Earliest departure",
    "departure_end": "Latest departure", "min_trip_days": "Shortest trip (days)",
    "max_trip_days": "Longest trip (days)", "currency": "Currency", "adults": "Travellers",
    "travel_class": "Cabin", "max_direction_minutes": "Max. travel time per direction (minutes)",
    "hide_separate_tickets": "Hide separate tickets", "carry_on_bags": "Cabin bag in the base search",
    "checked_bags": "Checked bag in the base search", "good_deal_nonstop_eur": "Price target, non-stop (€)",
    "good_deal_layover_eur": "Price target, with stops (€)", "drop_percent": "Strong deal: at least % below the low",
    "drop_eur": "Strong deal: at least € below the low", "history_window_days": "Comparison period (days)",
    "realert_improvement_eur": "New price alert from a change of (€)", "max_deals_per_run": "Max. offers per message",
    "pending_ttl_hours": "Undelivered messages expire after (hours)",
    "max_verifications_per_run": "Flight checks per run", "outbound_candidates": "Outbound candidates per check",
    "max_http_attempts_per_run": "Request budget per run", "max_run_seconds": "Time budget per run (seconds)",
    "http_timeout_seconds": "Timeout per request (seconds)", "http_attempts": "Attempts per request",
    "request_interval_seconds": "Gap between requests (seconds)", "max_parallel_requests": "Parallel requests",
    "airlines": "Only these airlines", "airlines_exclude": "Exclude these airlines",
    "max_stops": "Max. stops per direction", "display_names": "Display names",
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


def serialize(config):
    return json.dumps(config.file_dict(), indent=2, ensure_ascii=False) + "\n"


def use(path):
    commit, text = stored()
    if commit is None:
        print("Search settings: config.json from main")
        return
    config = Config.from_dict(json.loads(text))  # Fail closed rather than search a stale route.
    Path(path).write_text(text, encoding="utf-8")
    print(f"Search settings from the website form ({commit[:7]}): "
          f"{', '.join(config.origins)} -> {config.destination}")


def parse_issue(body):
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
    missing = sorted({f.name for f in fields(Config)} - set(OPTIONAL) - set(values))
    if missing:
        # Defaults would silently fall back to the original Bangkok search.
        raise Rejected("Settings are missing: " + ", ".join(missing))
    try:
        return Config.from_dict(values)
    except (ValueError, TypeError) as exc:
        raise Rejected(f"Invalid setting: {exc}") from None


def normalize(config):
    route = (*config.origins, config.destination)
    names = {code: name for code, name in config.display_names.items()
             if code in route and name != place(code)["city"]}
    return Config.from_dict({**config.form_dict(), "display_names": names})


def check(config, today):
    for code in (*config.origins, config.destination):
        if not supported(code):
            raise Rejected(f"The airport {code} is not supported by the flight search.")
    for code in (*config.airlines, *config.airlines_exclude):
        if not airline_supported(code):
            raise Rejected(f"The airline {code} is not supported by the flight search.")
    if not plan(config, today):
        raise Rejected("The departure window has no day from tomorrow on, so there would be nothing to search.")
    estimate = request_estimate(config, today)
    if estimate["requests"] > config.max_http_attempts_per_run:
        raise Rejected(f"Too many requests: about {estimate['requests']} per run, budget "
                       f"{config.max_http_attempts_per_run}. Choose fewer departure days, trip lengths or airports.")
    if estimate["seconds"] > config.max_run_seconds:
        raise Rejected(f"Search too long: about {estimate['seconds'] // 60} minutes per run, limit "
                       f"{config.max_run_seconds // 60} minutes. Choose fewer departure days, trip lengths or airports.")
    return estimate


def shown(name, value):
    if name == "origins":
        return ", ".join(value)
    if name in ("airlines", "airlines_exclude"):
        return ", ".join(value) or ("all" if name == "airlines" else "none")
    if name == "max_stops":
        return "any" if value is None else "non-stop only" if value == 0 else f"up to {value}"
    if name == "display_names":
        return ", ".join(f"{k}: {v}" for k, v in sorted(value.items())) or "automatic"
    if name == "travel_class":
        return CLASSES.get(value, value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


def cell(text):
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def changes(old, new):
    old_values, new_values = old.public_dict(), new.public_dict()
    return [(LABELS[f.name], shown(f.name, old_values[f.name]), shown(f.name, new_values[f.name]))
            for f in fields(Config) if old_values[f.name] != new_values[f.name]]


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
    current = Config.from_dict(json.loads(text)) if parent else Config.load(path)
    try:
        new = normalize(parse_issue(body))
        estimate = check(new, today)
    except Rejected as exc:
        return "rejected", (f"❌ **Not applied:** {exc}\n\n"
                            "The current search keeps running unchanged. Please correct it in the form and send it again.")
    diff = changes(current, new)
    if not diff:
        return "unchanged", "ℹ️ **No change:** These settings already match the current search."
    route = f"{', '.join(new.origins)} → {new.destination}"
    commit = save(serialize(new), parent, f"Search settings{f' from issue #{issue}' if issue else ''}: {route}\n")
    lines = ["✅ **New search applied:** " + route, "", "| Setting | Before | New |", "|---|---|---|"]
    lines += [f"| {cell(label)} | {cell(old)} | {cell(value)} |" for label, old, value in diff]
    lines.append("")
    if new.scope() != current.scope():
        lines += ["ℹ️ Settings that define comparable prices changed (destination, cabin, bags, separate "
                  "tickets, travel time limit, airlines or stops): price history and alerts start over for this "
                  "search. The old history stays stored.", ""]
    lines += [f"The website shows the new search in about 2 minutes. A search run has started (about "
              f"{estimate['requests']} requests); first prices appear after about 10–40 minutes.", "",
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

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
from tracker.config import Config
from tracker.places import place, supported
from tracker.planner import plan, request_estimate

BRANCH = "refs/heads/search-config"
MARKER = "<!-- flightwatch-search-settings -->"
MAX_BODY = 20000
LABELS = {
    "origins": "Abflughäfen", "destination": "Ziel", "departure_start": "Abflug frühestens",
    "departure_end": "Abflug spätestens", "min_trip_days": "Reisedauer mindestens (Tage)",
    "max_trip_days": "Reisedauer höchstens (Tage)", "currency": "Währung", "adults": "Personen",
    "travel_class": "Reiseklasse", "max_direction_minutes": "Max. Flugdauer je Richtung (Minuten)",
    "hide_separate_tickets": "Getrennte Tickets ausblenden", "carry_on_bags": "Kabinenkoffer in der Grundsuche",
    "checked_bags": "Aufgabegepäck in der Grundsuche", "good_deal_nonstop_eur": "Preisziel Direktflug (€)",
    "good_deal_layover_eur": "Preisziel mit Umstieg (€)", "drop_percent": "Starker Deal: mindestens % unter Tief",
    "drop_eur": "Starker Deal: mindestens € unter Tief", "history_window_days": "Vergleichszeitraum (Tage)",
    "realert_improvement_eur": "Neuer Preisalarm ab Änderung (€)", "max_deals_per_run": "Max. Angebote pro Meldung",
    "pending_ttl_hours": "Unzugestellte Meldungen verfallen nach (Stunden)",
    "max_verifications_per_run": "Flugprüfungen pro Suchlauf", "outbound_candidates": "Hinflug-Kandidaten je Prüfung",
    "max_http_attempts_per_run": "Anfragen-Budget pro Suchlauf", "max_run_seconds": "Zeitbudget pro Suchlauf (Sekunden)",
    "http_timeout_seconds": "Timeout je Anfrage (Sekunden)", "http_attempts": "Versuche je Anfrage",
    "request_interval_seconds": "Abstand zwischen Anfragen (Sekunden)", "max_parallel_requests": "Parallele Anfragen",
    "display_names": "Anzeigenamen",
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
        raise Rejected("Das Issue enthält keine Sucheinstellungen aus dem Formular.")
    block = re.search(r"```json[ \t]*\r?\n(.*?)\r?\n```", body, re.S)
    if not block:
        raise Rejected("Im Issue fehlt der Einstellungsblock (```json … ```).")
    try:
        values = json.loads(block.group(1))
    except ValueError:
        raise Rejected("Der Einstellungsblock ist kein gültiges JSON.") from None
    if not isinstance(values, dict):
        raise Rejected("Der Einstellungsblock muss ein JSON-Objekt sein.")
    missing = sorted({f.name for f in fields(Config)} - {"display_names"} - set(values))
    if missing:
        # Defaults would silently fall back to the original Bangkok search.
        raise Rejected("Es fehlen Einstellungen: " + ", ".join(missing))
    try:
        return Config.from_dict(values)
    except (ValueError, TypeError) as exc:
        raise Rejected(f"Ungültige Einstellung: {exc}") from None


def normalize(config):
    route = (*config.origins, config.destination)
    names = {code: name for code, name in config.display_names.items()
             if code in route and name != place(code)["city"]}
    return Config.from_dict({**config.file_dict(), "display_names": names})


def check(config, today):
    for code in (*config.origins, config.destination):
        if not supported(code):
            raise Rejected(f"Der Flughafen {code} wird von der Flugsuche nicht unterstützt.")
    if not plan(config, today):
        raise Rejected("Im Abflugfenster liegt kein Tag ab morgen; es gäbe nichts zu suchen.")
    estimate = request_estimate(config, today)
    if estimate["requests"] > config.max_http_attempts_per_run:
        raise Rejected(f"Zu viele Anfragen: etwa {estimate['requests']} pro Suchlauf, Budget "
                       f"{config.max_http_attempts_per_run}. Weniger Abflugtage, Reisedauern oder Flughäfen wählen.")
    if estimate["seconds"] > config.max_run_seconds:
        raise Rejected(f"Zu lange Suche: etwa {estimate['seconds'] // 60} Minuten pro Suchlauf, Limit "
                       f"{config.max_run_seconds // 60} Minuten. Weniger Abflugtage, Reisedauern oder Flughäfen wählen.")
    return estimate


def shown(name, value):
    if name == "origins":
        return ", ".join(value)
    if name == "display_names":
        return ", ".join(f"{k}: {v}" for k, v in sorted(value.items())) or "automatisch"
    if name == "travel_class":
        return CLASSES.get(value, value)
    if isinstance(value, bool):
        return "ja" if value else "nein"
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
        return "rejected", (f"❌ **Nicht übernommen:** {exc}\n\n"
                            "Die bisherige Suche läuft unverändert weiter. Bitte im Formular korrigieren und erneut senden.")
    diff = changes(current, new)
    if not diff:
        return "unchanged", "ℹ️ **Keine Änderung:** Diese Einstellungen entsprechen schon der aktuellen Suche."
    route = f"{', '.join(new.origins)} → {new.destination}"
    commit = save(serialize(new), parent, f"Search settings{f' from issue #{issue}' if issue else ''}: {route}\n")
    lines = ["✅ **Neue Suche übernommen:** " + route, "", "| Einstellung | Bisher | Neu |", "|---|---|---|"]
    lines += [f"| {cell(label)} | {cell(old)} | {cell(value)} |" for label, old, value in diff]
    lines.append("")
    if new.scope() != current.scope():
        lines += ["ℹ️ Ziel, Reiseklasse, Gepäck, getrennte Tickets oder Flugdauer-Limit haben sich geändert: "
                  "Preisverlauf und Preisalarme beginnen für diese Suche neu. Der alte Verlauf bleibt gespeichert.", ""]
    lines += [f"Die Website zeigt die neue Suche in etwa 2 Minuten. Ein Suchlauf ist gestartet (etwa "
              f"{estimate['requests']} Anfragen); erste Preise erscheinen nach etwa 10–40 Minuten.", "",
              f"Gespeichert im Branch `search-config` (Commit {commit[:7]}). `config.json` in main bleibt "
              "unverändert und gilt erst wieder, wenn du diesen Branch löschst."]
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

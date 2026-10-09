"""Check setup: a plain-language checklist of what a copy of the tracker still needs.

Run by the "Check setup" workflow. It reads repository settings through the GitHub
API (with the workflow's own token), never reads secret values (the workflow only
says whether each one is set) and changes nothing.
"""
from datetime import date
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tracker.config import Settings  # noqa: E402
from tracker.planner import plan  # noqa: E402
from tracker.project import dashboard_url, repository, repository_url  # noqa: E402

OK, TODO, NOTE = "✅", "❌", "⚠️"


def api(path):
    """GitHub API JSON, or None when the resource does not exist (e.g. Pages not set up)."""
    result = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    return json.loads(result.stdout) if result.returncode == 0 else None


def gather(config_path="config.json"):
    repo = repository()
    state = subprocess.run(["git", "ls-remote", "--exit-code", "--heads", "origin", "tracker-state"],
                           capture_output=True)
    try:
        trips = Settings.load(config_path).ordered()
        config_error = None
    except (OSError, ValueError) as exc:
        trips, config_error = [], str(exc)
    flag = lambda name: os.environ.get(name, "") == "true"
    return dict(repo=api(f"repos/{repo}") or {}, pages=api(f"repos/{repo}/pages"),
                state=state.returncode == 0, trips=trips, config_error=config_error,
                discord=flag("HAS_DISCORD"), telegram_token=flag("HAS_TELEGRAM_TOKEN"),
                telegram_chat=flag("HAS_TELEGRAM_CHAT"),
                channel=os.environ.get("NOTIFICATION_CHANNEL", "").strip().lower(),
                paused=os.environ.get("TRACKER_ENABLED", "").strip().lower() == "false")


def checks(facts, today):
    """(status, title, what to do) rows; `today` decides which trips can still be searched."""
    url, settings = repository_url(), f"{repository_url()}/settings"
    rows = []
    pages = facts["pages"]
    if not pages or pages.get("build_type") != "workflow":
        rows.append((TODO, "Website (GitHub Pages) is not set up",
                     f"Open [Settings → Pages]({settings}/pages) and choose **GitHub Actions** as the source. "
                     f"Then run [Publish dashboard]({url}/actions/workflows/pages.yml)."))
    else:
        rows.append((OK, "Website is published", f"[{pages.get('html_url') or dashboard_url()}]"
                                                 f"({pages.get('html_url') or dashboard_url()})"))
    if facts["repo"].get("has_issues") is False:
        rows.append((TODO, "Issues are turned off, so the settings form cannot apply changes",
                     f"Open [Settings → General]({settings}) → Features and tick **Issues**."))
    else:
        rows.append((OK, "The settings form can apply changes", "Use **Change search** on the website."))
    if facts["state"]:
        rows.append((OK, "Searches have run and price history is stored", ""))
    else:
        rows.append((TODO, "No search has run yet",
                     f"Open [Track flights]({url}/actions/workflows/track-flights.yml), click **Run workflow**. "
                     "After that searches start by themselves about every 6 hours."))
    if facts["paused"]:
        rows.append((NOTE, "Tracking is paused",
                     f"The repository variable `TRACKER_ENABLED` is `false`. Delete it under "
                     f"[Settings → Secrets and variables → Actions]({settings}/variables/actions) to resume."))
    if facts["config_error"]:
        rows.append((TODO, "The search settings are invalid", f"`{facts['config_error']}` – fix them on **Change search**."))
    for trip in facts["trips"]:
        route = f"{', '.join(trip.origins)} → {trip.destination}, {trip.departure_start} to {trip.departure_end}"
        if plan(trip, today):
            rows.append((OK, f"Trip {route} is being searched", ""))
        else:
            rows.append((NOTE, f"Trip {route} has no departure day left",
                         "Set new dates or another trip on **Change search** on the website."))
    channel, secrets = facts["channel"], f"{settings}/secrets/actions"
    telegram = facts["telegram_token"] and facts["telegram_chat"]
    if channel == "discord" and not facts["discord"] or channel == "telegram" and not telegram:
        rows.append((TODO, f"`NOTIFICATION_CHANNEL` is `{channel}`, but it is not set up",
                     f"Add the missing secret under [Secrets]({secrets}) or delete the variable."))
    elif facts["discord"] and channel != "telegram":
        rows.append((OK, "Price alerts go to Discord", ""))
    elif telegram:
        rows.append((OK, "Price alerts go to Telegram", ""))
    elif facts["telegram_token"] or facts["telegram_chat"]:
        rows.append((TODO, "Telegram is only half set up",
                     f"Both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are needed ([Secrets]({secrets})). "
                     f"[Telegram setup]({url}/actions/workflows/telegram-setup.yml) finds the chat ID."))
    else:
        rows.append((NOTE, "No price alerts yet (optional)",
                     f"Add a Discord webhook as the secret `DISCORD_WEBHOOK_URL` under [Secrets]({secrets}). "
                     "The website works without it."))
    return rows


def report(rows):
    lines = ["# Flight tracker setup", "", "| | Check | What to do |", "|---|---|---|"]
    lines += [f"| {status} | {title} | {todo.replace('|', '/')} |" for status, title, todo in rows]
    missing = sum(status == TODO for status, _, _ in rows)
    lines += ["", "Everything needed is set up." if not missing else
              f"{missing} step{'s' if missing > 1 else ''} left. Run **Check setup** again afterwards."]
    return "\n".join(lines) + "\n"


def main():
    rows = checks(gather(), date.today())
    text = report(rows)
    print(text)
    for status, title, _ in rows:
        if status == TODO:
            print(f"::warning::{title}")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text)


if __name__ == "__main__":
    main()

"""Tell the main message channel when publishing the dashboard fails, and when it works again.

Run by the last job of the Publish dashboard workflow with this run's result and the
result of the run before it. Only a change is reported: the first failure after a
success, and the first success after a failure. Repeated failures stay quiet, so a
broken build does not send a message after every search.
"""
import argparse
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tracker.network import ServiceError  # noqa: E402
from tracker.notifications import DASHBOARD, configured_sender, notifications_configured  # noqa: E402
from tracker.project import repository_url  # noqa: E402

FAILED = "Dashboard publishing failed"
RECOVERED = "Dashboard publishing recovered"


def message(result, previous, run_id=""):
    """The text to send for this run's result ("success" or "failure") after the previous
    run's ("success", "failure" or "" if unknown), or None."""
    details = f"\nDetails: {repository_url()}/actions/runs/{run_id}" if re.fullmatch(r"\d+", run_id or "") else ""
    if result == "failure" and previous != "failure":
        return (f"{FAILED}\nThe website was not updated and still shows the prices of its last update. "
                f"Searches and price alerts are not affected. You get one message when it works again.{details}")
    if result == "success" and previous == "failure":
        return f"{RECOVERED}\nThe website shows current prices again: {DASHBOARD}"
    return None


def main(argv=None, sender=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--result", required=True, choices=["success", "failure"])
    parser.add_argument("--previous", default="", help="result of the run before; empty if unknown")
    args = parser.parse_args(argv)
    text = message(args.result, args.previous.strip(), os.environ.get("GITHUB_RUN_ID", ""))
    if text is None:
        print(f"Publishing {args.result} after {args.previous or 'an unknown result'}: no message needed")
        return 0
    if sender is None:
        if not notifications_configured():
            print("::notice::No Discord or Telegram set up, so publishing problems are not sent.")
            return 0
        sender = configured_sender()
    try:
        sender.send(text)
    except ServiceError as exc:
        print(f"::warning::Could not send the publishing message: {exc}")
        return 1
    print(f"Sent: {text.splitlines()[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

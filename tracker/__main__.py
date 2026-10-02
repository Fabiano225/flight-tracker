import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sys

from .config import Settings
from .network import ServiceError
from .planner import plan
from .provider import FreeProvider, DemoProvider, GuardedClient, cache_file
from .report import report, export_history
from .service import scan_trips
from .store import Store, utcnow
from .telegram import Telegram
from .discord import Discord
from .notifications import configured_sender, deliver, message_labels, notifications_configured


def plan_summary(config, today):
    batches = plan(config, today)
    slots = sum(len(b.pairs()) for b in batches)
    return {"departure_date_pairs": slots // 2, "calendar_profile_slots": slots,
            "date_batches": len(batches), "date_searches": slots, "date_searches_30_days": slots*4*30,
            "verification_searches_max": config.max_verifications_per_run,
            "http_attempt_cap": config.max_http_attempts_per_run,
            "data_source": "Free unofficial Google Flights via flights==0.9.0",
            "api_subscription": "none", "max_minutes_per_direction": config.max_direction_minutes}


def main():
    parser = argparse.ArgumentParser(description="Free flight price tracker; dates refer to departure dates")
    parser.add_argument("command", choices=["plan", "scan", "demo", "notify", "telegram-test", "discord-test", "report", "export"])
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--state-dir")
    parser.add_argument("--as-of", help="YYYY-MM-DD; only offline plan/demo")
    parser.add_argument("--demo-discount", type=int, default=0)
    parser.add_argument("--output", default="history.csv")
    args = parser.parse_args()
    settings = Settings.load(args.config)
    trips = settings.ordered()
    if args.as_of and args.command not in {"plan", "demo"}:
        parser.error("--as-of is restricted to offline plan/demo")
    now = datetime.combine(date.fromisoformat(args.as_of), datetime.min.time(), timezone.utc) if args.as_of else utcnow()
    if args.command == "plan":
        plans = {trip.id: plan_summary(trip, now.date()) for trip in trips}
        # The request cap is shared by all trips of a run.
        print(json.dumps(plans[trips[0].id] if len(trips) == 1 else plans, indent=2))
        return 0
    if args.command == "telegram-test":
        Telegram().send("Flight tracker Telegram connection OK. Live deals require successful flight searches.")
        print("Telegram test message delivered")
        return 0
    if args.command == "discord-test":
        code, names = message_labels(settings)
        Discord(code=code, names=names).send("Discord connected\nThe flight tracker will send price alerts and check status to this channel.\nThis is a connection test, not a flight offer.")
        print("Discord test message delivered")
        return 0
    demo = args.command == "demo"
    directory = args.state_dir or ("demo-state" if demo else "state")
    with Store(directory, "demo" if demo else "live") as store:
        if args.command in {"scan", "demo"}:
            # One paced client for all trips: they share the run's request and time budget.
            http = None if demo else GuardedClient(trips[0])
            providers = (lambda trip: DemoProvider(trip, args.demo_discount)) if demo else (
                lambda trip: FreeProvider(trip, cache_path=cache_file(directory, trip), http=http))
            try:
                summaries = scan_trips(trips, store, providers, now, demo)
            finally:
                if http:
                    http.close()
            text = report(store)
            shown = [{k: v for k, v in summary.items() if k != "config"} for summary in summaries]
            print(json.dumps(shown[0] if len(shown) == 1 else shown, indent=2))
            if os.environ.get("GITHUB_STEP_SUMMARY"):
                with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
                    f.write(text)
            if demo:
                print("DEMO ONLY - no network or notification messages. Outbox contains synthetic alerts.")
            return 1 if any(summary["status"] == "partial" for summary in summaries) else 0
        if args.command == "notify":
            if not notifications_configured():
                # A new copy works without Discord or Telegram; the dashboard still updates.
                print("::notice::No Discord or Telegram set up, so price alerts are not sent. "
                      "See 'Get notifications' in README.md.")
                return 0
            print(f"Delivered {deliver(store, settings, now, configured_sender(settings))} messages")
        elif args.command == "report":
            print(report(store))
        elif args.command == "export":
            export_history(store, args.output)
            print(f"History exported to {Path(args.output).resolve()}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ServiceError, ValueError, OSError) as exc:
        print(f"Tracker error: {exc}", file=sys.stderr)
        sys.exit(1)

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import sys

from .config import Config
from .network import ServiceError
from .planner import plan
from .provider import FreeProvider, DemoProvider
from .report import report, export_history
from .service import scan
from .store import Store, utcnow
from .telegram import Telegram, deliver


def main():
    parser = argparse.ArgumentParser(description="Free BKK flight tracker; dates refer to departure dates")
    parser.add_argument("command", choices=["plan", "scan", "demo", "notify", "telegram-test", "report", "export"])
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--state-dir")
    parser.add_argument("--as-of", help="YYYY-MM-DD; only offline plan/demo")
    parser.add_argument("--demo-discount", type=int, default=0)
    parser.add_argument("--output", default="history.csv")
    args = parser.parse_args()
    config = Config.load(args.config)
    if args.as_of and args.command not in {"plan", "demo"}:
        parser.error("--as-of is restricted to offline plan/demo")
    now = datetime.combine(date.fromisoformat(args.as_of), datetime.min.time(), timezone.utc) if args.as_of else utcnow()
    if args.command == "plan":
        batches = plan(config, now.date())
        slots = sum(len(b.pairs()) for b in batches)
        print(json.dumps({"departure_date_pairs": slots // 2, "calendar_profile_slots": slots,
            "calendar_queries": len(batches), "calendar_queries_30_days": len(batches)*4*30,
            "verification_searches_max": config.max_verifications_per_run,
            "http_attempt_cap": config.max_http_attempts_per_run,
            "data_source": "Free unofficial Google Flights via flights==0.9.0",
            "api_subscription": "none", "max_minutes_per_direction": config.max_direction_minutes}, indent=2))
        return 0
    if args.command == "telegram-test":
        Telegram().send("Flight tracker Telegram connection OK. Live deals require successful flight searches.")
        print("Telegram test message delivered")
        return 0
    demo = args.command == "demo"
    directory = args.state_dir or ("demo-state" if demo else "state")
    with Store(directory, "demo" if demo else "live") as store:
        if args.command in {"scan", "demo"}:
            provider = DemoProvider(config, args.demo_discount) if demo else FreeProvider(config)
            summary = scan(config, store, provider, now, demo)
            text = report(store)
            print(json.dumps({k: v for k, v in summary.items() if k != "config"}, indent=2))
            if os.environ.get("GITHUB_STEP_SUMMARY"):
                with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
                    f.write(text)
            if demo:
                print("DEMO ONLY - no network or Telegram messages. Outbox contains synthetic alerts.")
            return 1 if summary["status"] == "partial" else 0
        if args.command == "notify":
            print(f"Delivered {deliver(store, config, now, Telegram())} messages")
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

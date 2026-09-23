import csv
import json
from pathlib import Path
from .alerts import hours


def report(store):
    row = store.db.execute("SELECT * FROM runs ORDER BY started DESC,rowid DESC LIMIT 1").fetchone()
    if not row:
        return "No runs recorded."
    summary = json.loads(row["summary"])
    quotes = [json.loads(r[0]) for r in store.db.execute("SELECT details FROM quotes WHERE run_id=? ORDER BY price", (row["id"],))]
    document = {**summary, "quotes": quotes}
    (store.directory / "latest.json").write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    with (store.directory / "latest.csv").open("w", newline="", encoding="utf-8") as f:
        keys = ["origin", "departure", "return_date", "category", "price_eur", "outbound_minutes", "inbound_minutes",
                "outbound_stops", "inbound_stops", "airlines", "link"]
        writer = csv.DictWriter(f, keys)
        writer.writeheader()
        for quote in quotes:
            item = dict(quote)
            item["price_eur"] = f"{item.pop('price') / 100:.2f}"
            # Keep the CSV schema stable as internal quote metadata grows.
            writer.writerow({key: item[key] for key in keys})
    config = summary["config"]
    lines = ["# BKK flight tracker", "", f"**{summary['mode'].upper()}** · {summary['started']} · **{summary['status']}**", "",
        f"One adult, {config['travel_class']}, EUR round-trip prices. Departure dates: {config['departure_start']} to {config['departure_end']}.",
        f"Trips last {config['min_trip_days']}–{config['max_trip_days']} days. Direction duration limit: {config['max_direction_minutes']} minutes including layovers.", "",
        f"- Date-search batches: {summary['calendar_queries_ok']}/{summary['calendar_queries_planned']}",
        f"- Dates recovered by deferred recheck: {summary.get('calendar_dates_recovered', 0)}",
        f"- Date-grid price points: {summary['calendar_prices']}/{summary['calendar_slots']} (nonstop and any-stops profiles)",
        f"- Itinerary searches: {summary['verification_searches']}; accepted quotes: {summary['verified_quotes']}",
        f"- HTTP attempts: {summary['http_attempts']}; queued price-alert items: {summary['queued_deals']}", "",
        "Date-grid prices are indicative. Only shortlisted round-trip itineraries with checked durations and stop counts enter alerts.",
        "The any-stops grid is not a layover-only grid. Actual itinerary stop counts determine NONSTOP or LAYOVER.", "",
        "| Origin | Departure | Return | Type | EUR | Out / back | Stops | Airlines |", "|---|---|---|---|---:|---|---|---|"]
    for q in quotes:
        lines.append(f"| {q['origin']} | {q['departure']} | {q['return_date']} | {q['category']} | {q['price']/100:.2f} | "
                     f"{hours(q['outbound_minutes'])} / {hours(q['inbound_minutes'])} | {q['outbound_stops']}/{q['inbound_stops']} | {q['airlines']} |")
    if summary["errors"]:
        lines += ["", "## Attention", *[f"- {e}" for e in summary["errors"]]]
    if summary["mode"] == "demo":
        lines += ["", "**All prices in this report are synthetic test data.**"]
    text = "\n".join(lines) + "\n"
    (store.directory / "report.md").write_text(text, encoding="utf-8")
    return text


def export_history(store, output):
    with Path(output).open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["observed_utc", "scope", "kind", "origin", "departure", "return", "profile_or_category", "price_eur"])
        for table, field in (("calendar", "profile"), ("quotes", "category")):
            for row in store.db.execute(f"SELECT observed,scope,origin,departure,return_date,{field},price FROM {table} ORDER BY observed"):
                writer.writerow([row[0], row[1], table, *row[2:6], f"{row[6]/100:.2f}" if row[6] is not None else ""])

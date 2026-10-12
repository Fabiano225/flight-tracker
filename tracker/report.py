import csv
import json
from pathlib import Path
from .alerts import hours, travellers
from .config import COUNTRIES


CSV_KEYS = ["origin", "departure", "return_date", "category", "price", "currency", "outbound_minutes", "inbound_minutes",
            "outbound_stops", "inbound_stops", "airlines", "link"]


def latest_runs(store, searches=None):
    """With `searches`: each one's latest run, in their order, so a scan of only some
    trips still reports every trip. Otherwise the runs of the latest scan."""
    if searches is not None:
        runs = (store.db.execute("SELECT * FROM runs WHERE scope=? ORDER BY started DESC,rowid DESC LIMIT 1",
                                 (search.scope(store.get_meta("mode") or "live"),)).fetchone() for search in searches)
        return list({run["id"]: run for run in runs if run}.values())
    row = store.db.execute("SELECT started FROM runs ORDER BY started DESC,rowid DESC LIMIT 1").fetchone()
    if not row:
        return []
    runs = {}
    for run in store.db.execute("SELECT * FROM runs WHERE started=? ORDER BY rowid", (row[0],)):
        runs[run["scope"]] = run
    return list(runs.values())


def section(summary, quotes, level):
    config = summary["config"]
    currency, country = config.get("currency", "EUR"), config.get("country", "DE")
    searched = f" Searched as from {COUNTRIES.get(country, country)}." if country != "DE" else ""
    lines = [f"{level} {config.get('destination', 'BKK')} flight tracker", "", f"**{summary['mode'].upper()}** · {summary['started']} · **{summary['status']}**", "",
        f"{travellers(config.get('adults', 1)).capitalize()}, {config['travel_class']}, {currency} round-trip prices per person.{searched} Departure dates: {config['departure_start']} to {config['departure_end']}.",
        f"Trips last {config['min_trip_days']}–{config['max_trip_days']} days"
        f"{', returning by ' + config['latest_return'] if config.get('latest_return') else ''}. Direction duration limit: {config['max_direction_minutes']} minutes including layovers.", "",
        f"- Date-search batches: {summary['calendar_queries_ok']}/{summary['calendar_queries_planned']}"
        f" (+{summary.get('calendar_queries_skipped', 0)} skipped: routes without prices, rechecked daily)",
        f"- Dates recovered by deferred recheck: {summary.get('calendar_dates_recovered', 0)}",
        f"- Date-grid price points: {summary['calendar_prices']}/{summary['calendar_slots']} (nonstop and any-stops profiles)",
        f"- Itinerary searches: {summary['verification_searches']}; accepted quotes: {summary['verified_quotes']}",
        f"- HTTP attempts: {summary['http_attempts']}; queued price-alert items: {summary['queued_deals']}", "",
        "Date-grid prices are indicative. Only shortlisted round-trip itineraries with checked durations and stop counts enter alerts.",
        "The any-stops grid is not a layover-only grid. Actual itinerary stop counts determine NONSTOP or LAYOVER.", "",
        f"| Origin | Departure | Return | Type | {currency} | Out / back | Stops | Airlines |", "|---|---|---|---|---:|---|---|---|"]
    for q in quotes:
        lines.append(f"| {q['origin']} | {q['departure']} | {q['return_date']} | {q['category']} | {q['price']/100:.2f} | "
                     f"{hours(q['outbound_minutes'])} / {hours(q['inbound_minutes'])} | {q['outbound_stops']}/{q['inbound_stops']} | {q['airlines']} |")
    if summary["errors"]:
        lines += ["", f"{level}# Attention", *[f"- {e}" for e in summary["errors"]]]
    return lines


def report(store, searches=None):
    """report.md, latest.json and latest.csv: every search's latest run (see latest_runs)."""
    runs = latest_runs(store, searches)
    if not runs:
        return "No runs recorded."
    documents = []
    for row in runs:
        quotes = [json.loads(r[0]) for r in store.db.execute("SELECT details FROM quotes WHERE run_id=? ORDER BY price", (row["id"],))]
        documents.append({**json.loads(row["summary"]), "quotes": quotes})
    several = len(documents) > 1
    latest = {"trips": documents} if several else documents[0]
    (store.directory / "latest.json").write_text(json.dumps(latest, indent=2) + "\n", encoding="utf-8")
    with (store.directory / "latest.csv").open("w", newline="", encoding="utf-8") as f:
        # Keep the CSV schema stable as internal quote metadata grows.
        keys = (["trip", "destination"] if several else []) + CSV_KEYS
        writer = csv.DictWriter(f, keys)
        writer.writeheader()
        for document in documents:
            for quote in document["quotes"]:
                item = dict(quote, trip=document.get("trip", "main"), destination=document["config"].get("destination"),
                            currency=document["config"].get("currency", "EUR"))
                item["price"] = f"{item['price'] / 100:.2f}"
                writer.writerow({key: item[key] for key in keys})
    lines = []
    if several:
        lines = [f"# Flight tracker · {len(documents)} trips", ""]
    for document in documents:
        lines += section(document, document["quotes"], "##" if several else "#") + [""]
    if documents[0]["mode"] == "demo":
        lines += ["**All prices in this report are synthetic test data.**", ""]
    text = "\n".join(lines[:-1]) + "\n"
    (store.directory / "report.md").write_text(text, encoding="utf-8")
    return text


def export_history(store, output):
    with Path(output).open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        # Prices are in the currency of the trip whose scope they belong to.
        writer.writerow(["observed_utc", "scope", "kind", "origin", "departure", "return", "profile_or_category", "price"])
        for table, field in (("calendar", "profile"), ("quotes", "category")):
            for row in store.db.execute(f"SELECT observed,scope,origin,departure,return_date,{field},price FROM {table} ORDER BY observed"):
                writer.writerow([row[0], row[1], table, *row[2:6], f"{row[6]/100:.2f}" if row[6] is not None else ""])

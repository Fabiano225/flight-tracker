"""Stable date watches: new date combinations alone must not cause deal spam."""
from datetime import date, timedelta
import hashlib
import json

from .alerts import hours, is_drop, search_link
from .config import cents
from .store import stamp


def watch_key(config, scope):
    window = [config.origins, config.departure_start, config.departure_end,
              config.min_trip_days, config.max_trip_days]
    if config.latest_return:  # Only when set, so existing watches keep their key.
        window.append(config.latest_return)
    return "trend-watch-v1:" + scope + ":" + hashlib.sha256(
        json.dumps(window).encode()).hexdigest()[:16]


def load_watches(store, config, scope, now):
    watches = json.loads(store.get_meta(watch_key(config, scope)) or "{}")
    watches = {key: value for key, value in watches.items()
               if value['origin'] in config.origins and value['departure'] > now.date().isoformat()
               and config.fits(value['departure'], value['return_date'])}
    # The search window is part of the metadata key, but expanding it must not
    # reset the dates behind an existing price notification. Recover the latest
    # eligible notification per group, also repairing pre-fix unannounced moves.
    alerts = store.db.execute("""SELECT a.* FROM alert_items a
        JOIN outbox o ON o.id=a.outbox_id
        WHERE a.scope=? AND o.kind='trend' AND o.status IN ('pending','sent')
        AND a.departure BETWEEN ? AND ? AND a.departure>?
        AND julianday(a.return_date)-julianday(a.departure) BETWEEN ? AND ?
        AND (? IS NULL OR a.return_date<=?)
        ORDER BY o.created DESC, o.rowid DESC""",
        (scope, config.departure_start, config.departure_end, now.date().isoformat(),
         config.min_trip_days, config.max_trip_days, config.latest_return, config.latest_return)).fetchall()
    seen = set()
    for alert in alerts:
        key = alert['origin'] + ':' + alert['category']
        if alert['origin'] not in config.origins or key in seen:
            continue
        seen.add(key)
        old = watches.get(key)
        if old and (old['departure'], old['return_date']) == (alert['departure'], alert['return_date']):
            continue
        row = store.db.execute("""SELECT details FROM quotes WHERE scope=? AND origin=?
            AND departure=? AND return_date=? AND category=?
            ORDER BY observed DESC, rowid DESC LIMIT 1""",
            (scope, alert['origin'], alert['departure'], alert['return_date'], alert['category'])).fetchone()
        if row:
            watches[key] = json.loads(row['details'])
    return watches


def watch_searches(watches):
    return [(v["origin"], v["departure"], v["return_date"],
             "nonstop" if v["category"] == "nonstop" else "any") for v in watches.values()]


def context(store, scope, quote, now, config, run_id):
    rows = store.db.execute("""SELECT price, observed FROM quotes
        WHERE scope=? AND origin=? AND departure=? AND return_date=? AND category=?
        AND observed>=? AND observed<=? AND run_id<>? ORDER BY observed DESC, rowid DESC""",
        (scope, quote.origin, quote.departure, quote.return_date, quote.category,
         stamp(now - timedelta(days=config.history_window_days)), stamp(now), run_id)).fetchall()
    return {"previous": rows[0]["price"] if rows else None,
            "previous_at": rows[0]["observed"] if rows else None,
            "low": min(r["price"] for r in rows) if rows else None,
            "count": len(rows), "first_at": rows[-1]["observed"] if rows else None}


def latest_alert(store, scope, quote, config):
    return store.db.execute("""SELECT a.*, o.created FROM alert_items a
        JOIN outbox o ON o.id=a.outbox_id
        WHERE a.scope=? AND a.origin=? AND a.category=? AND o.kind='trend'
        AND o.status IN ('pending','sent') AND a.departure BETWEEN ? AND ?
        AND julianday(a.return_date)-julianday(a.departure) BETWEEN ? AND ?
        AND (? IS NULL OR a.return_date<=?)
        ORDER BY o.created DESC, o.rowid DESC LIMIT 1""",
        (scope, quote.origin, quote.category, config.departure_start, config.departure_end,
         config.min_trip_days, config.max_trip_days, config.latest_return, config.latest_return)).fetchone()


def connections(stops):
    return "non-stop" if not stops else "via " + ", ".join(f"{airport} {hours(wait)}" for airport, wait in stops)


def flight_lines(quote):
    """One line per direction: local departure -> arrival (+days), travel time, connections."""
    times = quote.schedule if isinstance(quote.schedule, (list, tuple)) and len(quote.schedule) == 4 else None
    stops = quote.layovers if isinstance(quote.layovers, (list, tuple)) and len(quote.layovers) == 2 else None
    if times is None and stops is None:
        return [f"Out {hours(quote.outbound_minutes)} / back {hours(quote.inbound_minutes)}"]
    lines = []
    for i, (label, minutes) in enumerate((("Out", quote.outbound_minutes), ("Back", quote.inbound_minutes))):
        parts = []
        if times:
            leaves, lands = times[2 * i], times[2 * i + 1]
            shift = (date.fromisoformat(lands[:10]) - date.fromisoformat(leaves[:10])).days
            parts.append(f"{leaves[11:]} -> {lands[11:]}" + (f" ({shift:+d} day{'s' if abs(shift) > 1 else ''})" if shift else ""))
        parts.append(hours(minutes))
        if stops:
            parts.append(connections(stops[i]))
        lines.append(f"{label}: " + " | ".join(parts))
    return lines


def eur(price):
    return f"{price / 100:.2f} EUR"


def change(price, previous):
    delta = price - previous
    return f"{eur(previous)} -> {eur(price)} ({delta / 100:+.2f} EUR / {delta / previous:+.1%})"


def block(quote, history, prior, config):
    same_dates = prior is not None and (prior["departure"], prior["return_date"]) == (
        quote.departure, quote.return_date)
    if prior is None:
        title = "FIRST PRICE"
    elif not same_dates:
        title = "CHEAPER ALTERNATIVE - different dates"
    else:
        title = "PRICE DROPPED" if quote.price < prior["price"] else "PRICE ROSE"
    category = "NON-STOP (both directions)" if quote.category == "nonstop" else "WITH STOPS"
    lines = [f"{title} | {quote.origin}-{config.destination} | {category}",
             f"{quote.departure} to {quote.return_date} | {eur(quote.price)}"]
    if prior is not None:
        label = "Since alert " if same_dates else "Compared with the alerted alternative "
        lines.append(label + prior["created"][:16].replace("T", " ") + " UTC:")
        lines.append(change(quote.price, prior["price"]))
        if not same_dates:
            lines.append(f"Previous dates: {prior['departure']} to {prior['return_date']}")
    if history["previous"] is not None:
        lines.append("Last check of the same dates (" +
                     history["previous_at"][:16].replace("T", " ") + " UTC):")
        lines.append(change(quote.price, history["previous"]))
        lines.append(f"Previous {config.history_window_days}-day low: {eur(history['low'])}; "
                     f"{history['count']} checks since {history['first_at'][:10]}.")
    else:
        lines.append("No price history for these dates yet.")
    threshold = config.threshold(quote.category, quote.origin)
    if quote.price <= threshold:
        if history["low"] is not None and quote.price - history["low"] >= cents(config.realert_improvement_eur):
            lines.append(f"WITHIN BUDGET, but {eur(quote.price - history['low'])} above the previous low.")
        else:
            lines.append(f"CHECK TO BUY: within your {eur(threshold)} target.")
    else:
        lines.append(f"WATCH: {eur(quote.price - threshold)} above your target.")
    if is_drop(quote.price, history["low"], config):
        lines.append("STRONG DEAL: clear drop from the previous low.")
    elif history["low"] is not None and quote.price <= history["low"]:
        lines.append("At or below the previous observed low.")
    if history["count"] < 3:
        lines.append("Little history: judged mainly against your price target.")
    lines.extend(flight_lines(quote))
    lines.append(search_link(quote, config.destination, config.travel_class))
    return "\n".join(lines)


def queue_trends(store, config, scope, run_id, verified, now, demo=False):
    watches = load_watches(store, config, scope, now)
    groups = {}
    for quote in verified.values():
        groups.setdefault((quote.origin, quote.category), []).append(quote)
    eligible = []
    for (origin, category), quotes in sorted(groups.items()):
        key = origin + ":" + category
        old = watches.get(key)
        best = min(quotes, key=lambda q: (q.price, q.departure, q.return_date))
        watched = next((q for q in quotes if old and (q.departure, q.return_date) ==
                        (old["departure"], old["return_date"])), None)
        # Missing search results never mean the fare rose. Keep the same dates
        # unless a newly checked alternative is materially cheaper.
        if old:
            reference = watched.price if watched else old["price"]
            notified = latest_alert(store, scope, best, config)
            if notified is not None:
                reference = min(reference, notified["price"])
            selected = best if best.price <= reference - cents(config.realert_improvement_eur) else watched
            if selected is None:
                continue
        else:
            selected = best
        watches[key] = selected.to_dict()
        prior = latest_alert(store, scope, selected, config)
        history = context(store, scope, selected, now, config, run_id)
        same_dates = prior is not None and (prior["departure"], prior["return_date"]) == (
            selected.departure, selected.return_date)
        threshold = config.threshold(category, origin)
        # Small changes accumulate against the last notification, not every run.
        changed = prior is None or (
            same_dates and (abs(selected.price - prior["price"]) >= cents(config.realert_improvement_eur)
                or (selected.price <= threshold) != (prior["price"] <= threshold))) or (
            not same_dates and selected.price <= prior["price"] - cents(config.realert_improvement_eur))
        if changed:
            eligible.append((selected, block(selected, history, prior, config)))
    store.set_meta(watch_key(config, scope), json.dumps(watches))
    header = ("DEMO - synthetic prices\n" if demo else f"{config.destination} price alert\n") + stamp(now) + "\n"
    footer = ("\n\nEUR per person, round trip. Observed search prices, not a forecast. "
              "Same dates and category; the airline may differ. Limited selection. "
              "Check price, baggage and conditions before booking.")
    # Split only at group boundaries to keep every message within Telegram's cap.
    batch, texts = [], []
    for quote, text in eligible[:config.max_deals_per_run]:
        if texts and len(header + "\n\n".join(texts + [text]) + footer) > 4096:
            store.enqueue(run_id, "trend", now, header + "\n\n".join(texts) + footer, batch, scope)
            batch, texts = [], []
        batch.append(quote)
        texts.append(text)
    if texts:
        store.enqueue(run_id, "trend", now, header + "\n\n".join(texts) + footer, batch, scope)
    return min(len(eligible), config.max_deals_per_run)

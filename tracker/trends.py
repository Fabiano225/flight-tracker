"""Stable date watches: new date combinations alone must not cause deal spam."""
from datetime import timedelta
import hashlib
import json

from .alerts import hours, is_drop, search_link
from .config import cents
from .store import stamp


def watch_key(config, scope):
    window = [config.origins, config.departure_start, config.departure_end,
              config.min_trip_days, config.max_trip_days]
    return "trend-watch-v1:" + scope + ":" + hashlib.sha256(
        json.dumps(window).encode()).hexdigest()[:16]


def load_watches(store, config, scope, now):
    watches = json.loads(store.get_meta(watch_key(config, scope)) or "{}")
    return {key: value for key, value in watches.items() if value["departure"] > now.date().isoformat()}


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
    # Separate the new notification policy from legacy rotating deal digests.
    return store.db.execute("""SELECT a.*, o.created FROM alert_items a
        JOIN outbox o ON o.id=a.outbox_id
        WHERE a.scope=? AND a.origin=? AND a.category=? AND o.kind='trend'
        AND o.status IN ('pending','sent') AND a.departure BETWEEN ? AND ?
        AND julianday(a.return_date)-julianday(a.departure) BETWEEN ? AND ?
        ORDER BY o.created DESC, o.rowid DESC LIMIT 1""",
        (scope, quote.origin, quote.category, config.departure_start, config.departure_end,
         config.min_trip_days, config.max_trip_days)).fetchone()


def eur(price):
    return f"{price / 100:.2f} EUR"


def change(price, previous):
    delta = price - previous
    return f"{eur(previous)} -> {eur(price)} ({delta / 100:+.2f} EUR / {delta / previous:+.1%})"


def block(quote, history, prior, config):
    same_dates = prior is not None and (prior["departure"], prior["return_date"]) == (
        quote.departure, quote.return_date)
    if prior is None:
        title = "STARTWERT"
    elif not same_dates:
        title = "GUENSTIGERE ALTERNATIVE - andere Reisedaten"
    else:
        title = "PREIS GESUNKEN" if quote.price < prior["price"] else "PREIS GESTIEGEN"
    category = "DIREKT (beide Richtungen)" if quote.category == "nonstop" else "MIT UMSTIEG"
    lines = [f"{title} | {quote.origin}-{config.destination} | {category}",
             f"{quote.departure} bis {quote.return_date} | {eur(quote.price)}"]
    if prior is not None:
        label = "Seit Meldung " if same_dates else "Gegenueber gemeldeter Alternative "
        lines.append(label + prior["created"][:16].replace("T", " ") + " UTC:")
        lines.append(change(quote.price, prior["price"]))
        if not same_dates:
            lines.append(f"Vorherige Daten: {prior['departure']} bis {prior['return_date']}")
    if history["previous"] is not None:
        lines.append("Letzte Messung gleicher Daten (" +
                     history["previous_at"][:16].replace("T", " ") + " UTC):")
        lines.append(change(quote.price, history["previous"]))
        lines.append(f"Bisheriges {config.history_window_days}-Tage-Tief: {eur(history['low'])}; "
                     f"{history['count']} Messungen seit {history['first_at'][:10]}.")
    else:
        lines.append("Noch kein Preisverlauf fuer diese Reisedaten.")
    threshold = config.threshold(quote.category)
    if quote.price <= threshold:
        if history["low"] is not None and quote.price - history["low"] >= cents(config.realert_improvement_eur):
            lines.append(f"IM BUDGET, aber {eur(quote.price - history['low'])} ueber bisherigem Tief.")
        else:
            lines.append(f"KAUF PRUEFEN: innerhalb deiner {eur(threshold)}-Zielgrenze.")
    else:
        lines.append(f"BEOBACHTEN: {eur(quote.price - threshold)} ueber deiner Zielgrenze.")
    if is_drop(quote.price, history["low"], config):
        lines.append("STARKER DEAL: deutlicher Rueckgang gegenueber bisherigem Tief.")
    elif history["low"] is not None and quote.price <= history["low"]:
        lines.append("Am bisherigen beobachteten Tief oder darunter.")
    if history["count"] < 3:
        lines.append("Wenig Vergleichsdaten: Einordnung vor allem anhand deiner Preisgrenze.")
    lines.append(f"Hin {hours(quote.outbound_minutes)} / zurueck {hours(quote.inbound_minutes)}")
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
        threshold = config.threshold(category)
        # Small changes accumulate against the last notification, not every run.
        changed = prior is None or (
            same_dates and (abs(selected.price - prior["price"]) >= cents(config.realert_improvement_eur)
                or (selected.price <= threshold) != (prior["price"] <= threshold))) or (
            not same_dates and selected.price <= prior["price"] - cents(config.realert_improvement_eur))
        if changed:
            eligible.append((selected, block(selected, history, prior, config)))
    store.set_meta(watch_key(config, scope), json.dumps(watches))
    header = ("DEMO - synthetische Preise\n" if demo else "BKK Preisalarm\n") + stamp(now) + "\n"
    footer = ("\n\nEUR pro Person, Hin/Rueck. Beobachtete Suchpreise, keine Preisprognose. "
              "Gleiche Daten/Kategorie, ggf. andere Airline. Begrenzte Auswahl. "
              "Preis, Gepaeck und Bedingungen vor Buchung pruefen.")
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

from decimal import Decimal
from urllib.parse import urlencode

from .config import cents


def is_drop(price, baseline, config):
    return baseline is not None and baseline - price >= cents(config.drop_eur) and (
        Decimal(baseline - price) * 100 >= Decimal(baseline) * Decimal(str(config.drop_percent)))


def reason_for(price, baseline, category, config):
    reasons = []
    if price <= config.threshold(category):
        reasons.append("good deal")
    if is_drop(price, baseline, config):
        reasons.append(f"drop from EUR {baseline / 100:.2f} ({(baseline-price)/baseline:.0%})")
    return "; ".join(reasons)


def hours(minutes):
    return f"{minutes // 60}h{minutes % 60:02d}"


def search_link(quote, destination, cabin="economy"):
    # Compact research link, not a guaranteed booking offer or locked fare.
    query = f"Round trip flights {quote.origin} to {destination} {quote.departure} return {quote.return_date} {cabin} one adult"
    return "https://www.google.com/travel/flights?" + urlencode({"q": query, "curr": "EUR", "hl": "en"})


def digest(items, config, now, demo=False):
    heading = "DEMO - synthetic prices" if demo else "BKK flight deals - EUR, 1 adult, round trip"
    lines = [heading, f"Observed {now.strftime('%Y-%m-%d %H:%M UTC')}"]
    for quote, reason in items:
        label = "NONSTOP both ways" if quote.category == "nonstop" else f"LAYOVER (stops {quote.outbound_stops}/{quote.inbound_stops})"
        lines.extend(["", f"{quote.origin} - {config.destination} | {label}",
            f"{quote.departure} -> {quote.return_date} | EUR {quote.price / 100:.2f}",
            f"{quote.airlines} | outbound {hours(quote.outbound_minutes)} / return {hours(quote.inbound_minutes)}",
            reason, search_link(quote, config.destination, config.travel_class)])
    lines += ["", "Recheck price, baggage and fare conditions before booking. Source coverage is limited."]
    text = "\n".join(lines)
    if len(text) > 4096:
        raise ValueError("Telegram digest exceeds message limit")
    return text


def diverse_take(items, limit, key):
    groups = {}
    for item in items:
        groups.setdefault(key(item), []).append(item)
    result = []
    while groups and len(result) < limit:
        for group in list(groups):
            if len(result) == limit:
                break
            result.append(groups[group].pop(0))
            if not groups[group]:
                del groups[group]
    return result

from decimal import Decimal
from urllib.parse import urlencode

from .config import cents


def is_drop(price, baseline, config):
    return baseline is not None and baseline - price >= cents(config.drop_eur) and (
        Decimal(baseline - price) * 100 >= Decimal(baseline) * Decimal(str(config.drop_percent)))


def hours(minutes):
    return f"{minutes // 60}h{minutes % 60:02d}"


def travellers(adults):
    return "one adult" if adults == 1 else f"{adults} adults"


def search_link(quote, destination, cabin="economy", adults=1):
    # Compact research link, not a guaranteed booking offer or locked fare.
    query = (f"Round trip flights {quote.origin} to {destination} {quote.departure} return {quote.return_date} "
             f"{cabin} {travellers(adults)}")
    return "https://www.google.com/travel/flights?" + urlencode({"q": query, "curr": "EUR", "hl": "en"})


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

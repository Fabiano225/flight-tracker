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


def search_query(origin, destination, departure, return_date, cabin="economy", adults=1, return_from=None):
    """Compact research link, not a guaranteed booking offer or locked fare. An open-jaw
    trip (return_from) is asked for as a multi-city trip."""
    if return_from:
        query = (f"Multi-city flights {origin} to {destination} {departure}, {return_from} to {origin} {return_date} "
                 f"{cabin} {travellers(adults)}")
    else:
        query = f"Round trip flights {origin} to {destination} {departure} return {return_date} {cabin} {travellers(adults)}"
    return "https://www.google.com/travel/flights?" + urlencode({"q": query, "curr": "EUR", "hl": "en"})


def search_link(quote, destination, cabin="economy", adults=1, return_from=None):
    return search_query(quote.origin, destination, quote.departure, quote.return_date, cabin, adults, return_from)


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

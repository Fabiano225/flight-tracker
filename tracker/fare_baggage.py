"""Conservative decoding of itinerary-wide Google booking-offer baggage.

Wire fields checked against visible booking pages for Condor, Oman Air and THAI
(2026-09-22). Unknown enums/shapes remain unknown; never infer from an airline,
fare name, requested search filter, or an unchanged price. No weight is exposed
by this decoder. Source inclusion is not a guarantee of the final checkout price.
"""
from dataclasses import replace
import re

from .config import cents

SOURCE = 'google_booking_v1'
STATUSES = ('included', 'chargeable', 'not_included', 'unknown')


def allowance(block, status_index, count_index):
    result = dict(status='unknown', pieces=None, kg=None)
    if not isinstance(block, list) or len(block) < 7:
        return result
    code, counts = block[status_index], block[6]
    if type(code) is not int:
        return result
    count = counts[count_index] if isinstance(counts, list) and len(counts) > count_index else None
    if code == 1 and type(count) is int and 1 <= count <= 9:
        result.update(status='included', pieces=count)
    elif code == 3:
        result['status'] = 'chargeable'  # Fee amount is absent: do not invent it.
    elif code == 4 and count == 0 and type(count) is int:
        result.update(status='not_included', pieces=0)
    return result


def public_baggage(value):
    """Validate persisted metadata and export a strict allowlist, including on base views."""
    if not isinstance(value, dict) or value.get('source') != SOURCE:
        return None
    vendor = value.get('vendor')
    if not isinstance(vendor, str) or not re.fullmatch(r'[\w .&+()/-]{1,80}', vendor):
        return None
    result = dict(source=SOURCE, vendor=vendor, whole_trip=True)
    if value.get('whole_trip') is not True:
        return None
    for kind in ('cabin', 'checked'):
        bag = value.get(kind)
        if not isinstance(bag, dict) or bag.get('status') not in STATUSES:
            return None
        status, count = bag['status'], bag.get('pieces')
        if status == 'included' and not (type(count) is int and 1 <= count <= 9):
            return None
        if status == 'not_included' and not (type(count) is int and count == 0):
            return None
        # v1 never emits weights; don't trust arbitrary persisted kg values.
        result[kind] = dict(status=status, pieces=count if status in ('included', 'not_included') else None, kg=None)
    return result


def covers(value, profile):
    bags = public_baggage(value)
    required = {'cabin': ('cabin',), 'checked': ('checked',), 'both': ('cabin', 'checked')}
    return bool(bags and profile in required and all(bags[k]['status'] == 'included' for k in required[profile]))


def decode_booking_quotes(text, pair, quote):
    from fli.search._wire import iter_wrb_chunks
    from fli.search._decoders import _try_parse_booking_row
    expected = [(leg.airline.name.removeprefix('_'), str(leg.flight_number)) for direction in pair for leg in direction.legs]
    found = {}

    def walk(node):
        if not isinstance(node, list):
            return
        option = _try_parse_booking_row(node)
        if option is None:
            for child in node:
                walk(child)
            return
        if option.currency != 'EUR' or option.flights != expected:
            return
        try:
            price = cents(option.price)
        except (TypeError, ValueError):
            return
        block = node[21][7] if len(node) > 21 and isinstance(node[21], list) and len(node[21]) > 7 else None
        bags = public_baggage(dict(source=SOURCE, vendor=option.vendor_name, whole_trip=True,
            cabin=allowance(block, 4, 1), checked=allowance(block, 2, 0)))
        if bags:
            # Later streamed chunks supersede preliminary data for a vendor.
            found[(option.vendor_code, option.vendor_name)] = replace(quote, price=price, baggage=bags)

    for chunk in iter_wrb_chunks(text):
        walk(chunk)
    return list(found.values())


def booking_quotes(search, http, pair, filters, quote):
    """Capture only this bounded, read-only SDK request; never persist response URLs/tokens."""
    captured = []

    class Capture:
        def post(self, *args, **kwargs):
            response = http.post(*args, **kwargs)
            captured.append(response.text)
            return response

    previous = search.client
    try:
        search.client = Capture()
        search.get_booking_options(pair, filters, currency='EUR', language='en', country='DE')
    finally:
        search.client = previous
    return decode_booking_quotes(captured[-1], pair, quote) if captured else []

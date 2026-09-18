"""Free unofficial Google Flights adapter; no paid or mock fallbacks."""
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit, urlencode
import json
import threading
import time

from .config import cents
from .network import ServiceError, BudgetError


@dataclass(frozen=True)
class Quote:
    origin: str
    departure: str
    return_date: str
    category: str
    price: int
    outbound_minutes: int
    inbound_minutes: int
    outbound_stops: int
    inbound_stops: int
    airlines: str
    link: str

    def to_dict(self):
        return asdict(self)


class GuardedClient:
    """Bound every underlying library request, including return-leg expansion.

    Avoid the library's nested retry mechanism, and fail on RPC errors that its
    decoder otherwise interprets as an empty calendar. No proxy rotation or
    CAPTCHA handling is attempted.
    """
    def __init__(self, config, session=None, sleep=time.sleep):
        from curl_cffi.requests import Session
        self.session = session or Session()
        self.config, self.sleep = config, sleep
        self.used = 0
        self.lock = threading.Lock()
        self.deadline = time.monotonic() + config.max_run_seconds

    def post(self, url, data, **kwargs):
        # Calls made in fli's worker pool are deliberately serialized and paced.
        with self.lock:
            for attempt in range(self.config.http_attempts):
                if time.monotonic() + self.config.request_interval_seconds + 1 >= self.deadline:
                    raise BudgetError("Flight scan time budget exhausted; checkpointing completed searches")
                if self.used >= self.config.max_http_attempts_per_run:
                    raise BudgetError("Flight request budget exhausted")
                if self.used:
                    self.sleep(self.config.request_interval_seconds)
                self.used += 1
                try:
                    # The streaming named endpoint currently returns RPC error 13.
                    # Use the public page's shopping-prefetch RPC, with the same
                    # filter payload and locale. No cookies or API key required.
                    if "GetShoppingResults" in url:
                        request = json.loads(parse_qs(data)["f.req"][0])[1]
                        query = parse_qs(urlsplit(url).query)
                        params = {key: query[key][0] for key in ("hl", "curr", "gl") if key in query}
                        params["rpcids"] = "LqxFAb"
                        request_url = "https://www.google.com/_/FlightsFrontendUi/data/batchexecute?" + urlencode(params)
                        request_data = {"f.req": json.dumps([[["LqxFAb", request, None, "generic"]]])}
                    else:
                        request_url, request_data = url, data
                    response = self.session.post(request_url, data=request_data, impersonate="chrome", allow_redirects=False,
                        timeout=min(self.config.http_timeout_seconds, max(1, self.deadline - time.monotonic())),
                        headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"})
                except Exception:
                    if attempt + 1 == self.config.http_attempts:
                        raise ServiceError("Flight source network error") from None
                    self.sleep(2 ** attempt)
                    continue
                if response.status_code in {401, 403, 429}:
                    raise ServiceError(f"Flight source HTTP {response.status_code}; pause until next run")
                if response.status_code in {500, 502, 503, 504} and attempt + 1 < self.config.http_attempts:
                    self.sleep(2 ** attempt)
                    continue
                if response.status_code != 200:
                    raise ServiceError(f"Flight source HTTP {response.status_code}")
                text = response.text
                if not text.startswith(")]}'"):
                    raise ServiceError("Unexpected flight response; possible consent page or source change")
                # Validate the envelope before the permissive upstream decoder.
                from fli.search._wire import parse_first_wrb_payload
                if parse_first_wrb_payload(text) is None:
                    raise ServiceError("Google Flights RPC error or changed response; no price data received")
                return response

    def close(self):
        self.session.close()


class PrefetchDates:
    """Date-grid observations from individual round-trip shopping searches.

    The broken calendar-stream endpoint is deliberately not used. Each profile
    and date pair receives its own real search; no dates are sampled or invented.
    Return choices are still unselected, so these remain indicative fares only.
    """
    def __init__(self, client):
        from fli.search import SearchFlights
        self.shopping = SearchFlights()
        self.shopping.client = client

    def search(self, filters, **locale):
        from fli.models import FlightSearchFilters, SortBy
        from fli.search.dates import DatePrice
        current = date.fromisoformat(filters.from_date)
        end = date.fromisoformat(filters.to_date)
        rows = []
        while current <= end:
            returning = current + timedelta(days=filters.duration)
            segments = deepcopy(filters.flight_segments)
            segments[0].travel_date = current.isoformat()
            segments[1].travel_date = returning.isoformat()
            search = FlightSearchFilters(trip_type=filters.trip_type, passenger_info=filters.passenger_info,
                flight_segments=segments, stops=filters.stops, seat_type=filters.seat_type,
                max_duration=filters.max_duration, bags=filters.bags, sort_by=SortBy.CHEAPEST)
            offers = self.shopping._fetch_flights(search, capture_session=False, **locale) or []
            if any(offer.currency != "EUR" for offer in offers if offer.price is not None):
                raise ServiceError("Date-search currency is missing or differs from EUR")
            prices = [offer.price for offer in offers if offer.price is not None and offer.price > 0]
            if prices:
                rows.append(DatePrice(date=(datetime.combine(current, datetime.min.time()),
                    datetime.combine(returning, datetime.min.time())), price=min(prices), currency="EUR"))
            current += timedelta(days=1)
        return rows


class FreeProvider:
    def __init__(self, config):
        from fli.search import SearchFlights
        self.config = config
        self.http = GuardedClient(config)
        self.dates, self.flights = PrefetchDates(self.http), SearchFlights()
        self.flights.client = self.http

    def common(self, origin, departure, return_date, profile):
        from fli.models import Airport, PassengerInfo, SeatType, MaxStops, BagsFilter
        from fli.core.builders import build_flight_segments
        segments, trip = build_flight_segments(Airport[origin], Airport[self.config.destination], departure, return_date)
        seats = {"economy": SeatType.ECONOMY, "premium_economy": SeatType.PREMIUM_ECONOMY,
                 "business": SeatType.BUSINESS, "first_class": SeatType.FIRST}
        return dict(trip_type=trip, passenger_info=PassengerInfo(adults=1), flight_segments=segments,
                    stops=MaxStops.NON_STOP if profile == "nonstop" else MaxStops.ANY,
                    seat_type=seats[self.config.travel_class], max_duration=self.config.max_direction_minutes,
                    bags=BagsFilter(checked_bags=self.config.checked_bags, carry_on=bool(self.config.carry_on_bags)))

    def fetch(self, batch):
        from fli.models import DateSearchFilters
        pairs = batch.pairs()
        params = self.common(batch.origin, *pairs[0], batch.profile)
        filters = DateSearchFilters(**params, from_date=batch.start.isoformat(), to_date=batch.end.isoformat(), duration=batch.duration)
        try:
            rows = self.dates.search(filters, currency="EUR", language="en", country="DE")
        except ServiceError:
            raise
        except Exception:
            raise ServiceError("Calendar parser failed; source format may have changed") from None
        if rows is None:
            raise ServiceError("Calendar response contained no decodable date array")
        result = {p: None for p in pairs}
        for row in rows:
            if len(row.date) != 2:
                raise ServiceError("Expected round-trip calendar dates")
            pair = tuple(d.date().isoformat() for d in row.date)
            if pair not in result:
                continue
            if row.currency != "EUR":
                raise ServiceError("Calendar currency is missing or differs from EUR")
            result[pair] = cents(row.price)
        return result

    def verify(self, origin, departure, return_date, profile):
        from fli.models import FlightSearchFilters, SortBy
        filters = FlightSearchFilters(**self.common(origin, departure, return_date, profile), sort_by=SortBy.CHEAPEST)
        try:
            pairs = self.flights.search(filters, top_n=self.config.outbound_candidates,
                                        currency="EUR", language="en", country="DE")
        except ServiceError:
            raise
        except Exception:
            raise ServiceError("Itinerary search/parser failed") from None
        return normalize_pairs(pairs or [], origin, departure, return_date, profile, self.config,
            lambda pair: "https://www.google.com/travel/flights?" + urlencode({"q":
                f"Round trip flights {origin} to {self.config.destination} {departure} return {return_date} {self.config.travel_class} one adult",
                "curr": "EUR", "hl": "en"}))

    def close(self):
        self.http.close()


def normalize_pairs(pairs, origin, departure, return_date, profile, config, make_link):
    best = {}
    for pair in pairs:
        if not isinstance(pair, tuple) or len(pair) != 2:
            continue
        outbound, inbound = pair
        if any(x.currency != "EUR" or not x.legs for x in pair):
            continue
        if any(not 0 < x.duration <= config.max_direction_minutes for x in pair):
            continue
        if config.hide_separate_tickets and any(x.self_transfer is True for x in pair):
            continue
        endpoints = [(origin, config.destination, departure), (config.destination, origin, return_date)]
        if any(x.legs[0].departure_airport.name != src or x.legs[-1].arrival_airport.name != dst
               or x.legs[0].departure_datetime.date().isoformat() != day
               for x, (src, dst, day) in zip(pair, endpoints)):
            continue
        category = "nonstop" if outbound.stops == inbound.stops == 0 else "layover"
        if profile == "nonstop" and category != "nonstop":
            continue
        # On the return-selection response, price is the total round-trip fare.
        # Adding outbound.price would double-count; outbound.price is a minimum
        # over still-unselected return options, not a standalone one-way fare.
        try:
            price = cents(inbound.price)
        except (ValueError, TypeError):
            continue
        airlines = ", ".join(sorted({leg.airline.name for x in pair for leg in x.legs}))
        quote = Quote(origin, departure, return_date, category, price, outbound.duration, inbound.duration,
                      outbound.stops, inbound.stops, airlines, make_link(pair))
        if category not in best or price < best[category].price:
            best[category] = quote
    return list(best.values())


class DemoProvider:
    """Synthetic fixtures only; selected explicitly by the demo subcommand."""
    def __init__(self, config, discount=0):
        self.config, self.discount = config, discount
        self.http = type("Stats", (), {"used": 0})()

    def price(self, origin, dep, profile):
        route = {"DUS": 35, "FRA": 20, "AMS": 0}.get(origin, 30)
        return max(100, 600 + route + (int(dep[-2:]) % 9) * 14 + (120 if profile == "nonstop" else 0) - self.discount) * 100

    def fetch(self, batch):
        self.http.used += 1
        return {pair: self.price(batch.origin, pair[0], batch.profile) for pair in batch.pairs()}

    def verify(self, origin, departure, return_date, profile):
        self.http.used += 1
        category = "nonstop" if profile == "nonstop" else "layover"
        return [Quote(origin, departure, return_date, category, self.price(origin, departure, profile),
                      700 if category == "nonstop" else 990, 740 if category == "nonstop" else 1050,
                      int(category == "layover"), int(category == "layover"), "DEMO",
                      "https://www.google.com/travel/flights")]

    def close(self):
        pass

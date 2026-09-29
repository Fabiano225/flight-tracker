"""Free unofficial Google Flights adapter; no paid or mock fallbacks."""
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit, urlencode
import json
import hashlib
from pathlib import Path
import threading
import time

from .config import cents
from .network import ServiceError, BudgetError, TransientSourceError


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
    itinerary_id: str | None = None
    baggage: dict | None = None

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
        self.blocked = False
        self.lock = threading.Lock()
        self.deadline = time.monotonic() + config.max_run_seconds
        # Pace request *starts*, so network latency overlaps the pacing gap
        # instead of adding to it. A few requests (fli's return-leg worker
        # pool) may be in flight at once; the request rate stays bounded.
        self.interval = config.request_interval_seconds
        self.next_start = 0.0
        self.slots = threading.BoundedSemaphore(config.max_parallel_requests)

    def reserve(self):
        """Atomically check budgets and claim the next paced start time."""
        with self.lock:
            if self.blocked:
                raise BudgetError("Flight source requested a pause; deferred to next scheduled run")
            now = time.monotonic()
            start = max(now, self.next_start) if self.used else now
            if start + 1 >= self.deadline:
                raise BudgetError("Flight scan time budget exhausted; checkpointing completed searches")
            if self.used >= self.config.max_http_attempts_per_run:
                raise BudgetError("Flight request budget exhausted")
            self.used += 1
            self.next_start = start + self.interval
        return start - now

    def slow_down(self):
        # Transient source trouble: widen the pacing gap (at most 5 s) ...
        with self.lock:
            self.interval = max(self.interval, min(self.interval * 2 or 1.0, 5.0))

    def recover(self):
        # ... and narrow it again on success, so one isolated error cannot
        # slow down the rest of the run.
        with self.lock:
            self.interval = max(self.config.request_interval_seconds, self.interval / 2)

    def post(self, url, data, **kwargs):
        with self.slots:
            return self.attempt(url, data)

    def attempt(self, url, data):
        for attempt in range(self.config.http_attempts):
            wait = self.reserve()
            if wait > 0:
                self.sleep(wait)
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
                self.slow_down()
                self.sleep(2 ** attempt)
                continue
            if response.status_code in {401, 403, 429}:
                self.blocked = True
                raise BudgetError(f"Flight source HTTP {response.status_code}; pause until next run")
            if response.status_code in {500, 502, 503, 504} and attempt + 1 < self.config.http_attempts:
                self.slow_down()
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
                code = rpc_error_code(text)
                # 13 is the standard RPC INTERNAL status, not an empty fare
                # result. Back off; never retry explicit access/rate denials.
                if code == 13 and attempt + 1 < self.config.http_attempts:
                    self.slow_down()
                    self.sleep(3 * (attempt + 1))
                    continue
                if code in {7, 8, 16}:
                    self.blocked = True
                    raise BudgetError(f"Flight source RPC {code}; pause until next run")
                if code == 13:
                    raise TransientSourceError("Google Flights RPC 13; no price data received after retries")
                raise ServiceError(f"Google Flights RPC {code if code is not None else 'error'} or changed response; no price data received")
            self.recover()
            return response

    def close(self):
        self.session.close()


def rpc_error_code(text):
    """Read only the sanitized numeric status from the observed error envelope."""
    try:
        rows = json.loads(text[4:].strip())
        for row in rows:
            if isinstance(row, list) and len(row) > 5 and row[0] == "wrb.fr" and row[2] is None:
                if isinstance(row[5], list) and row[5] and type(row[5][0]) is int:
                    return row[5][0]
    except (ValueError, TypeError, IndexError):
        pass
    return None


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
        self.recovered_dates = 0

    def search(self, filters, **locale):
        from fli.models import FlightSearchFilters, SortBy
        from fli.search.dates import DatePrice
        current = date.fromisoformat(filters.from_date)
        end = date.fromisoformat(filters.to_date)
        rows = []
        pending = []

        def fetch_day(day):
            returning = day + timedelta(days=filters.duration)
            segments = deepcopy(filters.flight_segments)
            segments[0].travel_date = day.isoformat()
            segments[1].travel_date = returning.isoformat()
            search = FlightSearchFilters(trip_type=filters.trip_type, passenger_info=filters.passenger_info,
                flight_segments=segments, stops=filters.stops, seat_type=filters.seat_type,
                max_duration=filters.max_duration, bags=filters.bags, sort_by=SortBy.CHEAPEST)
            offers = self.shopping._fetch_flights(search, capture_session=False, **locale) or []
            if any(offer.currency != "EUR" for offer in offers if offer.price is not None):
                raise ServiceError("Date-search currency is missing or differs from EUR")
            prices = [offer.price for offer in offers if offer.price is not None and offer.price > 0]
            if prices:
                rows.append(DatePrice(date=(datetime.combine(day, datetime.min.time()),
                    datetime.combine(returning, datetime.min.time())), price=min(prices), currency="EUR"))

        while current <= end:
            try:
                fetch_day(current)
            except TransientSourceError as exc:
                # Keep successful dates in memory. Only isolated INTERNAL errors
                # qualify; denials, malformed data and budget errors propagate.
                pending.append(current)
                if len(pending) >= 3:
                    raise TransientSourceError("Multiple date searches returned RPC 13; stopping this batch") from exc
            current += timedelta(days=1)

        if pending:
            # One deferred pass, at most two dates per batch. All requests still
            # use the same run deadline, request cap and paced transport.
            self.shopping.client.sleep(30)
            for day in pending:
                try:
                    fetch_day(day)
                except TransientSourceError as exc:
                    raise TransientSourceError(f"{day.isoformat()}: RPC 13 persisted after deferred recheck") from exc
                self.recovered_dates += 1
        return rows


class FreeProvider:
    CACHE_MAX_AGE_SECONDS = 3600

    def __init__(self, config, cache_path=None):
        from fli.search import SearchFlights
        self.config = config
        self.http = GuardedClient(config)
        self.dates, self.flights = PrefetchDates(self.http), SearchFlights()
        self.flights.client = self.http
        # Itinerary searches of this run, reused by the later baggage step so
        # it does not repeat identical requests. Local file only; never published.
        self.cache_path = Path(cache_path) if cache_path else None
        self.searches = {}
        self.cache_stats = {"cached_searches": 0, "cache_fallbacks": 0}

    @staticmethod
    def search_key(origin, departure, return_date, profile):
        return "|".join((origin, departure, return_date, profile))

    def save_search_cache(self, run_id):
        if not self.cache_path:
            return
        entries = {key: {"session": session, "pairs": [[x.model_dump(mode="json") for x in pair] for pair in pairs]}
                   for key, (pairs, session) in self.searches.items()}
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"run_id": run_id, "scope": self.config.scope(), "created": time.time(),
                                   "entries": entries}), encoding="utf-8")
        tmp.replace(self.cache_path)

    def load_search_cache(self, run_id):
        """Accept only this run's fresh, fully decodable cache; otherwise search live."""
        from fli.models import FlightResult
        self.searches = {}
        if not self.cache_path or not self.cache_path.is_file():
            return
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if (data["run_id"] != run_id or data["scope"] != self.config.scope()
                    or not 0 <= time.time() - data["created"] <= self.CACHE_MAX_AGE_SECONDS):
                return
            self.searches = {key: ([tuple(FlightResult.model_validate(x) for x in pair) for pair in entry["pairs"]],
                                   entry["session"]) for key, entry in data["entries"].items()}
        except Exception:
            self.searches = {}

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
        if pairs and self.flights._last_session_id:
            self.searches[self.search_key(origin, departure, return_date, profile)] = (pairs, self.flights._last_session_id)
        return normalize_pairs(pairs or [], origin, departure, return_date, profile, self.config,
            lambda pair: "https://www.google.com/travel/flights?" + urlencode({"q":
                f"Round trip flights {origin} to {self.config.destination} {departure} return {return_date} {self.config.travel_class} one adult",
                "curr": "EUR", "hl": "en"}))

    def baggage_offers(self, origin, departure, return_date, profile):
        """Inspect actual vendor offers, not prices from a requested bag filter."""
        cached = self.searches.pop(self.search_key(origin, departure, return_date, profile), None)
        if cached:
            # Reuse the base scan's search and its Google session. If that
            # yields nothing usable, fall back to a fresh live search.
            try:
                found = self.booking_offers(origin, departure, return_date, profile, cached)
            except BudgetError:
                raise
            except ServiceError:
                found = []
            if found:
                self.cache_stats["cached_searches"] += 1
                return found
            self.cache_stats["cache_fallbacks"] += 1
        return self.booking_offers(origin, departure, return_date, profile)

    def booking_offers(self, origin, departure, return_date, profile, cached=None):
        from fli.models import FlightSearchFilters, SortBy
        from .fare_baggage import booking_quotes
        filters = FlightSearchFilters(**self.common(origin, departure, return_date, profile), sort_by=SortBy.CHEAPEST)
        try:
            if cached:
                pairs, self.flights._last_session_id = cached
            else:
                pairs = self.flights.search(filters, top_n=self.config.outbound_candidates,
                                            currency="EUR", language="en", country="DE") or []
            candidates = []
            for pair in pairs:
                quotes = normalize_pairs([pair], origin, departure, return_date, profile, self.config,
                    lambda _: "https://www.google.com/travel/flights?" + urlencode({"q":
                        f"Round trip flights {origin} to {self.config.destination} {departure} return {return_date} economy one adult",
                        "curr": "EUR", "hl": "en"}))
                if quotes and quotes[0].itinerary_id:
                    candidates.append((quotes[0], pair))
            # Inspect one cheapest return per outbound first, rather than using
            # the entire small detail budget on near-identical return options.
            selected, departures = [], set()
            ordered = sorted(candidates, key=lambda x: x[0].price)
            for candidate in ordered:
                departure_id = itinerary_id((candidate[1][0],))
                if departure_id not in departures:
                    departures.add(departure_id)
                    selected.append(candidate)
            selected += [x for x in ordered if x not in selected]
            found = []
            for q, pair in selected[:self.config.outbound_candidates]:
                found.extend(booking_quotes(self.flights, self.http, pair, filters, q))
            return found
        except ServiceError:
            raise
        except Exception:
            raise ServiceError("Baggage tariff details unavailable or changed format") from None

    def close(self):
        self.http.close()


def itinerary_id(pair):
    """Compare actual flight legs, never merely dates or airline names."""
    legs = []
    for direction in pair:
        group = []
        for leg in direction.legs:
            if not getattr(leg, 'flight_number', None):
                return None
            group.append([leg.airline.name, leg.flight_number, leg.departure_airport.name,
                          leg.arrival_airport.name, leg.departure_datetime.isoformat()])
        legs.append(group)
    return hashlib.sha256(json.dumps(legs).encode()).hexdigest()


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
                      outbound.stops, inbound.stops, airlines, make_link(pair), itinerary_id(pair))
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

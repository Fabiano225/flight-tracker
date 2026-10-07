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

from .alerts import search_query
from .config import cents, per_person
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
    # Local times at each airport: outbound departs, arrives, return departs, arrives.
    schedule: tuple | None = None
    # Connections per direction (outbound, return): (airport code, minutes waiting) each.
    layovers: tuple | None = None

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
                max_duration=filters.max_duration, airlines=filters.airlines,
                airlines_exclude=filters.airlines_exclude, bags=filters.bags, sort_by=SortBy.CHEAPEST)
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

    def __init__(self, config, cache_path=None, http=None):
        from fli.search import SearchFlights
        self.config = config
        # Trips of one run share a client, its request budget and its pacing.
        self.owns_http = http is None
        self.http = http or GuardedClient(config)
        self.dates, self.flights = PrefetchDates(self.http), SearchFlights()
        self.flights.client = self.http
        # Itinerary searches of this run, reused by the later baggage step so
        # it does not repeat identical requests. Local file only; never published.
        self.cache_path = Path(cache_path) if cache_path else None
        self.searches = {}
        self.cache_stats = {"cached_searches": 0, "cache_fallbacks": 0}
        self.stats_lock = threading.Lock()
        # fli keeps per-search state (session ID, swapped client), so worker
        # threads each get their own SearchFlights over the shared paced client.
        self.owner, self.local = threading.get_ident(), threading.local()

    def searcher(self):
        if threading.get_ident() == self.owner:
            return self.flights
        flights = getattr(self.local, "flights", None)
        if flights is None:
            from fli.search import SearchFlights
            flights = self.local.flights = SearchFlights()
            flights.client = self.http
        return flights

    def count(self, name):
        with self.stats_lock:
            self.cache_stats[name] += 1

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
        from fli.models import Airline, Airport, PassengerInfo, SeatType, MaxStops, BagsFilter, TimeRestrictions
        from fli.core.builders import build_flight_segments, build_multi_city_segments
        if self.config.return_from:
            # Open jaw: out to the destination, back from another airport.
            segments, trip = build_multi_city_segments([
                (Airport[origin], Airport[self.config.destination], departure),
                (Airport[self.config.return_from], Airport[origin], return_date)])
        else:
            segments, trip = build_flight_segments(Airport[origin], Airport[self.config.destination], departure, return_date)
        # Google gets each window widened to whole hours (it may read "until 21" as 21:59);
        # normalize_pairs then keeps only flights inside the exact windows.
        for segment, direction in zip(segments, ("outbound", "return")):
            dep, arr = (self.config.flight_times.get(f"{direction}_{kind}") for kind in ("departure", "arrival"))
            if dep or arr:
                hours = lambda w: (w[0] or None, None if w[1] == 24 else w[1]) if w else (None, None)
                (a, b), (c, d) = hours(dep), hours(arr)
                segment.time_restrictions = TimeRestrictions(earliest_departure=a, latest_departure=b,
                                                             earliest_arrival=c, latest_arrival=d)
        seats = {"economy": SeatType.ECONOMY, "premium_economy": SeatType.PREMIUM_ECONOMY,
                 "business": SeatType.BUSINESS, "first_class": SeatType.FIRST}
        stops = {None: MaxStops.ANY, 0: MaxStops.NON_STOP, 1: MaxStops.ONE_STOP_OR_FEWER,
                 2: MaxStops.TWO_OR_FEWER_STOPS}[self.config.max_stops]
        # The library names codes that start with a digit "_4U".
        airline = lambda code: Airline[code] if code in Airline.__members__ else Airline["_" + code]
        return dict(trip_type=trip, passenger_info=PassengerInfo(adults=self.config.adults), flight_segments=segments,
                    stops=MaxStops.NON_STOP if profile == "nonstop" else stops,
                    seat_type=seats[self.config.travel_class], max_duration=self.config.max_direction_minutes,
                    airlines=[airline(c) for c in self.config.airlines] or None,
                    airlines_exclude=[airline(c) for c in self.config.airlines_exclude] or None,
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
            result[pair] = per_person(cents(row.price), self.config.adults)
        return result

    def verify(self, origin, departure, return_date, profile):
        from fli.models import FlightSearchFilters, SortBy
        filters = FlightSearchFilters(**self.common(origin, departure, return_date, profile), sort_by=SortBy.CHEAPEST)
        flights = self.searcher()
        try:
            pairs = flights.search(filters, top_n=self.config.outbound_candidates,
                                   currency="EUR", language="en", country="DE")
        except ServiceError:
            raise
        except Exception:
            raise ServiceError("Itinerary search/parser failed") from None
        # Private fli field: if a library update renames it, skip caching
        # (the baggage step then searches live) instead of failing the scan.
        session = getattr(flights, "_last_session_id", None)
        if pairs and isinstance(session, str) and session:
            self.searches[self.search_key(origin, departure, return_date, profile)] = (pairs, session)
        link = search_query(origin, self.config.destination, departure, return_date, self.config.travel_class,
                            self.config.adults, self.config.return_from)
        return normalize_pairs(pairs or [], origin, departure, return_date, profile, self.config, lambda _: link)

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
                self.count("cached_searches")
                return found
            self.count("cache_fallbacks")
        return self.booking_offers(origin, departure, return_date, profile)

    def booking_offers(self, origin, departure, return_date, profile, cached=None):
        from fli.models import FlightSearchFilters, SortBy
        from .fare_baggage import booking_quotes
        filters = FlightSearchFilters(**self.common(origin, departure, return_date, profile), sort_by=SortBy.CHEAPEST)
        flights = self.searcher()
        try:
            if cached:
                pairs, flights._last_session_id = cached
            else:
                pairs = flights.search(filters, top_n=self.config.outbound_candidates,
                                       currency="EUR", language="en", country="DE") or []
            candidates = []
            for pair in pairs:
                link = search_query(origin, self.config.destination, departure, return_date, "economy",
                                    self.config.adults, self.config.return_from)
                quotes = normalize_pairs([pair], origin, departure, return_date, profile, self.config, lambda _: link)
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
                found.extend(booking_quotes(flights, self.http, pair, filters, q))
            return found
        except ServiceError:
            raise
        except Exception:
            raise ServiceError("Baggage tariff details unavailable or changed format") from None

    def close(self):
        if self.owns_http:
            self.http.close()


def cache_file(directory, config):
    """Each trip's searches are cached in a file of its own, per destination."""
    return Path(directory) / f"search-cache-{config.id}-{config.destination}.json"


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


def schedule(pair):
    """Local departure and arrival times of both directions ("YYYY-MM-DDTHH:MM"),
    or None if the source no longer provides them."""
    try:
        times = [moment for direction in pair
                 for moment in (direction.legs[0].departure_datetime, direction.legs[-1].arrival_datetime)]
        return tuple(moment.strftime("%Y-%m-%dT%H:%M") for moment in times)
    except (AttributeError, TypeError, ValueError):
        return None


def layovers(pair):
    """Connection airports and waiting times (minutes) of both directions, or None
    if the source no longer provides them. A change of airport reads "LHR/LGW"."""
    try:
        result = []
        for direction in pair:
            stops = []
            for arriving, leaving in zip(direction.legs, direction.legs[1:]):
                wait = (leaving.departure_datetime - arriving.arrival_datetime).total_seconds() // 60
                if wait < 0:
                    return None
                here, there = arriving.arrival_airport.name, leaving.departure_airport.name
                stops.append((here if here == there else f"{here}/{there}", int(wait)))
            result.append(tuple(stops))
        return tuple(result)
    except (AttributeError, TypeError, ValueError):
        return None


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
        endpoints = [(origin, config.destination, departure), (config.return_from or config.destination, origin, return_date)]
        if any(x.legs[0].departure_airport.name != src or x.legs[-1].arrival_airport.name != dst
               or x.legs[0].departure_datetime.date().isoformat() != day
               for x, (src, dst, day) in zip(pair, endpoints)):
            continue
        category = "nonstop" if outbound.stops == inbound.stops == 0 else "layover"
        if profile == "nonstop" and category != "nonstop":
            continue
        # Google applies the stop and airline filters; recheck so a changed
        # source cannot slip excluded flights into prices and alerts.
        if config.max_stops is not None and max(outbound.stops, inbound.stops) > config.max_stops:
            continue
        carriers = {leg.airline.name.removeprefix("_") for x in pair for leg in x.legs}
        if carriers & set(config.airlines_exclude) or (config.airlines and not carriers & set(config.airlines)):
            continue
        times = schedule(pair)
        if not config.times_fit(times):
            continue
        # On the return-selection response, price is the total round-trip fare.
        # Adding outbound.price would double-count; outbound.price is a minimum
        # over still-unselected return options, not a standalone one-way fare.
        # With several travellers it covers all of them; prices are kept per person.
        try:
            price = per_person(cents(inbound.price), config.adults)
        except (ValueError, TypeError):
            continue
        airlines = ", ".join(sorted(carriers))
        quote = Quote(origin, departure, return_date, category, price, outbound.duration, inbound.duration,
                      outbound.stops, inbound.stops, airlines, make_link(pair), itinerary_id(pair),
                      schedule=times, layovers=layovers(pair))
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
        out_minutes, in_minutes = (700, 740) if category == "nonstop" else (990, 1050)
        # Synthetic local times: the destination is assumed 5 hours ahead.
        leaves, returns = datetime.fromisoformat(f"{departure}T10:15"), datetime.fromisoformat(f"{return_date}T23:50")
        times = (leaves, leaves + timedelta(minutes=out_minutes + 300),
                 returns, returns + timedelta(minutes=in_minutes - 300))
        connections = ((("DOH", 115),), (("DOH", 125),)) if category == "layover" else ((), ())
        return [Quote(origin, departure, return_date, category, self.price(origin, departure, profile),
                      out_minutes, in_minutes, int(category == "layover"), int(category == "layover"), "DEMO",
                      "https://www.google.com/travel/flights",
                      schedule=tuple(t.strftime("%Y-%m-%dT%H:%M") for t in times), layovers=connections)]

    def close(self):
        pass

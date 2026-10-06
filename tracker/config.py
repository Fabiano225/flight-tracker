from dataclasses import dataclass, asdict, field, fields
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
from pathlib import Path
import re


def cents(value):
    if isinstance(value, bool):
        raise ValueError("Price must be a number")
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount <= 0 or amount > 10_000_000:
            raise ValueError("Price outside supported range")
        result = int((amount * 100).quantize(Decimal("1")))
        if result < 1:
            raise ValueError("Price must be at least one cent")
        return result
    except InvalidOperation:
        raise ValueError("Invalid price") from None


# Integer settings and their accepted ranges, shared with the website's settings form.
INT_LIMITS = {
    "min_trip_days": (1, 90), "max_trip_days": (1, 90),
    "history_window_days": (1, 365), "max_deals_per_run": (1, 6),
    "pending_ttl_hours": (1, 24), "max_http_attempts_per_run": (1, 2000),
    "http_timeout_seconds": (1, 120), "http_attempts": (1, 4),
    "carry_on_bags": (0, 1), "checked_bags": (0, 1),
    "max_direction_minutes": (1, 1259), "max_verifications_per_run": (6, 100),
    "outbound_candidates": (1, 10),
    "max_run_seconds": (60, 2400), "max_parallel_requests": (1, 6),
}
FLOAT_LIMITS = {"drop_percent": (0.01, 100), "request_interval_seconds": (0, 30)}
PRICE_FIELDS = ("good_deal_nonstop_eur", "good_deal_layover_eur", "drop_eur", "realert_improvement_eur")
TRAVEL_CLASSES = ("economy", "premium_economy", "business", "first_class")
MAX_DISPLAY_NAME = 40
MAX_AIRLINES = 25
MAX_TRIPS = 5
# The original single search. Its price history scope has no trip id, so it
# keeps the history it collected before several trips were possible.
MAIN_TRIP = "main"
TRIP_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,19}")
# Run-wide settings: all trips of a run share one request budget and one message queue.
SHARED_FIELDS = ("pending_ttl_hours", "max_http_attempts_per_run", "max_run_seconds", "http_timeout_seconds",
                 "http_attempts", "request_interval_seconds", "max_parallel_requests")
# Optional settings: config.json leaves them out while they have these values.
OPTIONAL = {"latest_return": None, "max_stops": None, "airlines": (), "airlines_exclude": (), "origin_targets": {}, "display_names": {},
            "id": MAIN_TRIP}
TARGET_CATEGORIES = ("nonstop", "layover")


@dataclass(frozen=True)
class Config:
    origins: tuple = ("DUS", "FRA", "AMS")
    destination: str = "BKK"
    departure_start: str = "2026-10-14"
    departure_end: str = "2026-10-23"
    min_trip_days: int = 14
    max_trip_days: int = 21
    # Optional last return date (YYYY-MM-DD): date pairs returning later are not searched.
    latest_return: str | None = None
    currency: str = "EUR"
    adults: int = 1
    travel_class: str = "economy"
    max_direction_minutes: int = 1259
    hide_separate_tickets: bool = True
    carry_on_bags: int = 0
    checked_bags: int = 0
    # Search filters, applied by Google: None allows any number of stops.
    max_stops: int | None = None
    airlines: tuple = ()
    airlines_exclude: tuple = ()
    good_deal_nonstop_eur: float = 650
    good_deal_layover_eur: float = 650
    # Optional price targets per departure airport, e.g. {"AMS": {"layover": 510}};
    # airports or categories without one use the two targets above.
    origin_targets: dict = field(default_factory=dict)
    drop_percent: float = 10
    drop_eur: float = 50
    history_window_days: int = 30
    realert_improvement_eur: float = 25
    max_deals_per_run: int = 6
    pending_ttl_hours: int = 12
    max_verifications_per_run: int = 18
    outbound_candidates: int = 3
    max_http_attempts_per_run: int = 1600
    max_run_seconds: int = 2400
    http_timeout_seconds: int = 60
    http_attempts: int = 3
    request_interval_seconds: float = 0.8
    max_parallel_requests: int = 3
    # Website and message labels only (airport code -> place name); never searched.
    display_names: dict = field(default_factory=dict)
    # Trip identifier when several trips are searched (website links, settings).
    id: str = MAIN_TRIP

    def __post_init__(self):
        if not isinstance(self.id, str) or not TRIP_ID.fullmatch(self.id):
            raise ValueError("Trip id must be 1-20 lowercase letters, digits or dashes")
        if not self.origins or len(set(self.origins)) != len(self.origins):
            raise ValueError("Origins must be a nonempty unique list")
        for airport in (*self.origins, self.destination):
            if not isinstance(airport, str) or not re.fullmatch(r"[A-Z]{3}", airport):
                raise ValueError("Use uppercase airport codes")
        if self.destination in self.origins:
            raise ValueError("Origin and destination must differ")
        start, end = date.fromisoformat(self.departure_start), date.fromisoformat(self.departure_end)
        if not 0 <= (end - start).days <= 365:
            raise ValueError("Departure window must span 1 to 366 dates")
        for name, (low, high) in INT_LIMITS.items():
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"Invalid {name}: expected integer {low}..{high}")
        if self.min_trip_days > self.max_trip_days:
            raise ValueError("Trip duration range is reversed")
        if self.latest_return is not None:
            # Strictly YYYY-MM-DD: date pairs are compared with it as text.
            if not isinstance(self.latest_return, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", self.latest_return):
                raise ValueError("latest_return must be a date (YYYY-MM-DD) or null")
            if (date.fromisoformat(self.latest_return) - start).days < self.min_trip_days:
                raise ValueError("latest_return is too early: even the shortest trip from the earliest departure returns later")
        # A single adult removes ambiguous per-person vs party-total pricing.
        if self.adults != 1 or isinstance(self.adults, bool) or self.currency != "EUR":
            raise ValueError("This tracker supports one adult and EUR prices")
        if self.travel_class not in TRAVEL_CLASSES:
            raise ValueError("Invalid travel class")
        if type(self.hide_separate_tickets) is not bool:
            raise ValueError("hide_separate_tickets must be boolean")
        for name, (low, high) in FLOAT_LIMITS.items():
            value = getattr(self, name)
            if type(value) not in {int, float} or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"Invalid {name}")
        for name in PRICE_FIELDS:
            cents(getattr(self, name))
        if self.max_stops is not None and (type(self.max_stops) is not int or not 0 <= self.max_stops <= 2):
            raise ValueError("Invalid max_stops: expected null or an integer 0..2")
        for name in ("airlines", "airlines_exclude"):
            codes = getattr(self, name)
            if (not isinstance(codes, tuple) or len(codes) > MAX_AIRLINES or len(set(codes)) != len(codes)
                    or not all(isinstance(c, str) and re.fullmatch(r"[A-Z0-9]{2}", c) for c in codes)):
                raise ValueError(f"{name} must list up to {MAX_AIRLINES} unique two-character airline codes")
        if set(self.airlines) & set(self.airlines_exclude):
            raise ValueError("An airline cannot be both included and excluded")
        if not isinstance(self.origin_targets, dict):
            raise ValueError("origin_targets must map departure airports to price targets")
        for code, targets in self.origin_targets.items():
            if code not in self.origins:
                raise ValueError(f"origin_targets: {code} is not a departure airport of this trip")
            if (not isinstance(targets, dict) or not targets
                    or not set(targets) <= set(TARGET_CATEGORIES)):
                raise ValueError(f"origin_targets for {code} must set nonstop and/or layover")
            for value in targets.values():
                cents(value)
        if not isinstance(self.display_names, dict):
            raise ValueError("display_names must map airport codes to names")
        for code, name in self.display_names.items():
            if not re.fullmatch(r"[A-Z]{3}", code):
                raise ValueError("display_names keys must be uppercase airport codes")
            if (not isinstance(name, str) or not 0 < len(name) <= MAX_DISPLAY_NAME
                    or name != name.strip() or not name.isprintable()):
                raise ValueError(f"Invalid display name for {code}")

    @classmethod
    def load(cls, path):
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    @classmethod
    def from_dict(cls, values):
        if not isinstance(values, dict):
            raise ValueError("Configuration must be a JSON object")
        unknown = set(values) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError(f"Unknown config keys: {', '.join(sorted(unknown))}")
        for name in ("origins", "airlines", "airlines_exclude"):
            if name in values:
                if not isinstance(values[name], list):
                    raise ValueError(f"{name} must be a list")
                values = {**values, name: tuple(values[name])}
        return cls(**values)

    def scope(self, mode="live"):
        # Filters that alter price comparability must isolate their history.
        names = ("destination", "currency", "adults", "travel_class", "max_direction_minutes",
                 "hide_separate_tickets", "carry_on_bags", "checked_bags")
        data = {name: getattr(self, name) for name in names}
        # Filters join the scope only when set, so existing histories keep their scope.
        for name in ("max_stops", "airlines", "airlines_exclude"):
            if getattr(self, name) != OPTIONAL[name]:
                data[name] = sorted(getattr(self, name)) if name != "max_stops" else self.max_stops
        # Further trips never share history or alerts with another trip, even to the same place.
        if self.id != MAIN_TRIP:
            data["trip"] = self.id
        data.update(provider="fli-0.9-prefetch-v2", mode=mode, gl="DE", hl="en")
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()[:24]

    def public_dict(self):
        return asdict(self)

    def form_dict(self):
        # JSON shape of every setting: field order, lists instead of tuples.
        return {**self.public_dict(), "origins": list(self.origins), "airlines": list(self.airlines),
                "airlines_exclude": list(self.airlines_exclude)}

    def file_dict(self):
        # config.json layout: optional settings appear only when they are set.
        return {name: value for name, value in self.form_dict().items()
                if name not in OPTIONAL or getattr(self, name) != OPTIONAL[name]}

    def fits(self, departure, return_date):
        """Whether a departure/return date pair (YYYY-MM-DD) belongs to this trip's search."""
        days = (date.fromisoformat(return_date) - date.fromisoformat(departure)).days
        return (self.departure_start <= departure <= self.departure_end
                and self.min_trip_days <= days <= self.max_trip_days
                and (self.latest_return is None or return_date <= self.latest_return))

    def threshold(self, category, origin=None):
        """Price target in cents: the departure airport's own one, if set, else the trip's."""
        own = self.origin_targets.get(origin, {}).get(category)
        if own is not None:
            return cents(own)
        return cents(self.good_deal_nonstop_eur if category == "nonstop" else self.good_deal_layover_eur)


def shared_values(config):
    return {name: getattr(config, name) for name in SHARED_FIELDS}


@dataclass(frozen=True)
class Settings:
    """One to five trips, searched one after another in each run.

    Every trip has its own route, dates, filters and price targets. The request
    budget, pacing and message expiry (SHARED_FIELDS) apply to the whole run.
    A config.json without a "trips" list is a single trip.
    """
    trips: tuple
    primary_trip: str = MAIN_TRIP

    def __post_init__(self):
        if (not isinstance(self.trips, tuple) or not 1 <= len(self.trips) <= MAX_TRIPS
                or not all(isinstance(trip, Config) for trip in self.trips)):
            raise ValueError(f"Settings need 1 to {MAX_TRIPS} trips")
        ids = [trip.id for trip in self.trips]
        if len(set(ids)) != len(ids):
            raise ValueError("Trip ids must be unique")
        if self.primary_trip not in ids:
            raise ValueError("primary_trip must name one of the trips")
        if any(shared_values(trip) != shared_values(self.trips[0]) for trip in self.trips):
            raise ValueError("All trips must use the same request settings")

    @property
    def primary(self):
        """The trip the website shows first."""
        return self.trip(self.primary_trip)

    def trip(self, trip_id):
        return next(trip for trip in self.trips if trip.id == trip_id)

    def ordered(self):
        """Search order: the primary trip first, then the others as listed."""
        return (self.primary, *(trip for trip in self.trips if trip.id != self.primary_trip))

    @classmethod
    def load(cls, path):
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    @classmethod
    def from_dict(cls, values):
        if not isinstance(values, dict):
            raise ValueError("Configuration must be a JSON object")
        if "trips" not in values:
            config = Config.from_dict(values)
            return cls((config,), config.id)
        unknown = set(values) - {"trips", "primary_trip", *SHARED_FIELDS}
        if unknown:
            raise ValueError(f"Unknown settings keys: {', '.join(sorted(unknown))}")
        trips = values["trips"]
        if not isinstance(trips, list) or not 1 <= len(trips) <= MAX_TRIPS:
            raise ValueError(f"trips must list 1 to {MAX_TRIPS} trips")
        shared = {name: values[name] for name in SHARED_FIELDS if name in values}
        configs = []
        for trip in trips:
            if not isinstance(trip, dict) or "id" not in trip:
                raise ValueError("Every trip needs an id")
            both = set(trip) & set(SHARED_FIELDS)
            if both:
                raise ValueError(f"{', '.join(sorted(both))} applies to all trips; set it once next to the trips list")
            configs.append(Config.from_dict({**trip, **shared}))
        primary = values.get("primary_trip", configs[0].id)
        if not isinstance(primary, str):
            raise ValueError("primary_trip must be a trip id")
        return cls(tuple(configs), primary)

    def file_dict(self):
        """config.json layout; a single trip keeps the flat original layout."""
        if len(self.trips) == 1:
            return self.trips[0].file_dict()
        trips = [{"id": trip.id, **{name: value for name, value in trip.file_dict().items()
                                    if name not in SHARED_FIELDS and name != "id"}} for trip in self.trips]
        return {"primary_trip": self.primary_trip, "trips": trips, **shared_values(self.trips[0])}


def trips_of(settings):
    """The trips of Settings; a single Config is its own only trip."""
    return settings.trips if isinstance(settings, Settings) else (settings,)

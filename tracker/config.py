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
# Optional settings: config.json leaves them out while they have these values.
OPTIONAL = {"max_stops": None, "airlines": (), "airlines_exclude": (), "display_names": {}}


@dataclass(frozen=True)
class Config:
    origins: tuple = ("DUS", "FRA", "AMS")
    destination: str = "BKK"
    departure_start: str = "2026-10-14"
    departure_end: str = "2026-10-23"
    min_trip_days: int = 14
    max_trip_days: int = 21
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

    def __post_init__(self):
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

    def threshold(self, category):
        return cents(self.good_deal_nonstop_eur if category == "nonstop" else self.good_deal_layover_eur)

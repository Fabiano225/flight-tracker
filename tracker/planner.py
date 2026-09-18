from dataclasses import dataclass
from datetime import date, timedelta


def days(start, end):
    while start <= end:
        yield start
        start += timedelta(days=1)


@dataclass(frozen=True)
class Batch:
    origin: str
    profile: str
    start: date
    end: date
    duration: int

    def pairs(self):
        return [(d.isoformat(), (d + timedelta(days=self.duration)).isoformat()) for d in days(self.start, self.end)]


def plan(config, today):
    start = max(date.fromisoformat(config.departure_start), today + timedelta(days=1))
    end = date.fromisoformat(config.departure_end)
    batches = []
    # Interleave routes, and keep each calendar query within the library's 61-day bound.
    while start <= end:
        last = min(end, start + timedelta(days=60))
        for duration in range(config.min_trip_days, config.max_trip_days + 1):
            for origin in config.origins:
                for profile in ("nonstop", "any"):
                    batches.append(Batch(origin, profile, start, last, duration))
        start = last + timedelta(days=1)
    return batches

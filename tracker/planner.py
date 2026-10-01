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
    # A non-stop-only search has nothing to find in the any-stops profile.
    profiles = ("nonstop",) if config.max_stops == 0 else ("nonstop", "any")
    # Interleave routes, and keep each calendar query within the library's 61-day bound.
    while start <= end:
        last = min(end, start + timedelta(days=60))
        for duration in range(config.min_trip_days, config.max_trip_days + 1):
            for origin in config.origins:
                for profile in profiles:
                    batches.append(Batch(origin, profile, start, last, duration))
        start = last + timedelta(days=1)
    return batches


def request_estimate(config, today):
    """Upper bound of one scan's requests: one per departure day, trip length,
    airport and stop profile, plus a full verification budget (one outbound and
    `outbound_candidates` return searches each). Retries come on top."""
    calendar = sum(len(b.pairs()) for b in plan(config, today))
    verification = config.max_verifications_per_run * (1 + config.outbound_candidates)
    requests = calendar + verification
    # Starts are paced; parallel requests overlap an assumed 1.5 s response time.
    seconds = requests * max(config.request_interval_seconds, 1.5 / config.max_parallel_requests)
    return {"calendar": calendar, "verification": verification, "requests": requests, "seconds": round(seconds)}

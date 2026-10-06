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
    latest_return = date.fromisoformat(config.latest_return) if config.latest_return else None
    while start <= end:
        last = min(end, start + timedelta(days=60))
        for duration in range(config.min_trip_days, config.max_trip_days + 1):
            # With a latest return date, longer trips must leave earlier.
            final = min(last, latest_return - timedelta(days=duration)) if latest_return else last
            if final < start:
                continue
            for origin in config.origins:
                for profile in profiles:
                    batches.append(Batch(origin, profile, start, final, duration))
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


def settings_estimate(settings, today):
    """Estimate of one run over all trips; they share the request and time budget."""
    trips = {trip.id: request_estimate(trip, today) for trip in settings.trips}
    return {"trips": trips, "requests": sum(e["requests"] for e in trips.values()),
            "seconds": sum(e["seconds"] for e in trips.values())}

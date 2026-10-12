"""Source errors: a flaky airport does not stop the others, and only real gaps count."""
from dataclasses import replace
from datetime import timedelta
import tempfile
import unittest
from unittest.mock import Mock

from tracker.config import Config
from tracker.network import BudgetError, TransientSourceError
from tracker.provider import DemoProvider
from tracker.service import AIRPORT_PAUSE_SECONDS, incomplete
from tracker.store import Store
from test_tracker import NOW, scan

# Three airports, two departure days, two trip lengths: 4 batches of 2 dates per airport.
CONFIG = replace(Config(), departure_start="2026-10-15", departure_end="2026-10-16", min_trip_days=14, max_trip_days=15)


class FlakyProvider(DemoProvider):
    """Demo prices, except where told to fail or leave dates unanswered."""
    def __init__(self, config, failing=(), missed=None):
        super().__init__(config)
        self.failing, self.missing, self.missed = set(failing), missed, []
        self.http.sleep = Mock()
        self.asked = []

    def fetch(self, batch):
        self.asked.append(batch.origin)
        if batch.origin in self.failing:
            raise TransientSourceError("Google Flights RPC 13; no price data received after retries")
        result = super().fetch(batch)
        self.missed = self.missing(batch) if self.missing else []
        for departure in self.missed:
            result[next(pair for pair in result if pair[0] == departure)] = None
        return result

    def verify(self, origin, departure, return_date, profile):
        if origin in self.failing:
            raise TransientSourceError("Google Flights RPC 13; no price data received after retries")
        return super().verify(origin, departure, return_date, profile)


class ResilienceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name, "demo")
        self.addCleanup(self.store.close)

    def test_a_failing_airport_is_left_out_and_the_others_continue(self):
        provider = FlakyProvider(CONFIG, failing=("DUS",))
        summary = scan(CONFIG, self.store, provider, NOW, True)
        # DUS is asked three times, then left out; FRA and AMS are searched completely.
        self.assertEqual(provider.asked.count("DUS"), 3)
        self.assertEqual((provider.asked.count("FRA"), provider.asked.count("AMS")), (4, 4))
        self.assertIn("DUS: date searches stopped after repeated errors; the other airports continue", summary["errors"])
        self.assertNotIn("Circuit breaker", " ".join(summary["errors"]))
        # One pause before going on with the other airports.
        provider.http.sleep.assert_called_once_with(AIRPORT_PAUSE_SECONDS)
        self.assertGreater(summary["verified_quotes"], 0)
        # A third of the dates is missing: a real gap.
        self.assertEqual((summary["status"], summary["incomplete"], summary["incomplete_in_row"]), ("partial", True, 1))

    def test_a_source_block_still_stops_everything(self):
        provider = FlakyProvider(CONFIG)
        provider.fetch = Mock(side_effect=BudgetError("Flight source HTTP 429; pause until next run"))
        summary = scan(CONFIG, self.store, provider, NOW, True)
        self.assertEqual(provider.fetch.call_count, 1)
        self.assertTrue(summary["incomplete"])

    def test_single_gaps_are_not_an_incomplete_search(self):
        first = lambda batch: [batch.pairs()[0][0]] if (batch.origin, batch.duration, batch.profile) == ("FRA", 14, "any") else []
        provider = FlakyProvider(CONFIG, missed=first)
        summary = scan(CONFIG, self.store, provider, NOW, True)
        self.assertIn("FRA/any/14d: 1 of 2 dates got no answer", summary["errors"])
        self.assertEqual(summary["calendar_answered"], summary["calendar_slots"] - 1)
        self.assertEqual((summary["status"], summary["incomplete"], summary["incomplete_in_row"]), ("partial", False, 0))
        # No "needs attention" message for a single gap.
        self.assertIsNone(self.store.db.execute("SELECT 1 FROM outbox WHERE kind='health'").fetchone())

    def test_incomplete_searches_in_a_row_are_counted_and_reset(self):
        failing = FlakyProvider(CONFIG, failing=("DUS", "FRA", "AMS"))
        self.assertEqual(scan(CONFIG, self.store, failing, NOW, True)["incomplete_in_row"], 1)
        failing = FlakyProvider(CONFIG, failing=("DUS", "FRA", "AMS"))
        self.assertEqual(scan(CONFIG, self.store, failing, NOW + timedelta(hours=1), True)["incomplete_in_row"], 2)
        healthy = scan(CONFIG, self.store, FlakyProvider(CONFIG), NOW + timedelta(hours=2), True)
        self.assertEqual((healthy["status"], healthy["incomplete"], healthy["incomplete_in_row"]), ("ok", False, 0))
        health = [row[0] for row in self.store.db.execute("SELECT text FROM outbox WHERE kind='health' ORDER BY rowid")]
        self.assertEqual(len(health), 2)
        self.assertTrue(health[0].startswith("Flight tracker needs attention"))
        self.assertTrue(health[1].startswith("Flight tracker recovered"))

    def test_what_counts_as_incomplete(self):
        base = dict(calendar_queries_planned=24, calendar_prices=400, calendar_slots=480, calendar_answered=480,
                    verification_planned=18, verification_searches=18, verification_failed=0, verified_quotes=20)
        self.assertFalse(incomplete(base))
        self.assertTrue(incomplete({**base, "calendar_prices": 0}))
        self.assertTrue(incomplete({**base, "verified_quotes": 0}))
        self.assertFalse(incomplete({**base, "calendar_answered": 400}))   # 80 of 480 missing.
        self.assertTrue(incomplete({**base, "calendar_answered": 359}))    # More than a quarter.
        self.assertFalse(incomplete({**base, "verification_failed": 4}))
        self.assertTrue(incomplete({**base, "verification_failed": 5}))
        self.assertTrue(incomplete({**base, "verification_searches": 12, "verification_failed": 0}))
        # One of two dates missing is a single gap, not a quarter of a search.
        self.assertFalse(incomplete({**base, "calendar_slots": 2, "calendar_answered": 1}))
        self.assertFalse(incomplete({**base, "calendar_queries_planned": 0, "calendar_prices": 0}))


if __name__ == "__main__":
    unittest.main()

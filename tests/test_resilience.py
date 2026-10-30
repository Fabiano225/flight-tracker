"""Source errors: a flaky airport does not stop the others, and only real gaps count."""
from dataclasses import replace
from datetime import date, timedelta
import tempfile
import unittest
from unittest.mock import Mock

from tracker.config import Config
from tracker.network import BudgetError, TransientSourceError
from tracker.provider import DemoProvider
from tracker.service import AIRPORT_PAUSE_SECONDS, gaps_key, incomplete, spread_checks
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
        self.asked, self.batches = [], []

    def fetch(self, batch):
        self.asked.append(batch.origin)
        self.batches.append(batch)
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


    def calendar(self, run_id):
        return {(row[0], row[1], row[2], row[3]) for row in self.store.db.execute(
            "SELECT origin, departure, return_date, profile FROM calendar WHERE run_id=?", (run_id,))}

    def test_unanswered_dates_are_asked_first_in_the_next_run(self):
        first = lambda batch: [batch.pairs()[0][0]] if (batch.origin, batch.duration, batch.profile) == ("FRA", 14, "any") else []
        provider = FlakyProvider(CONFIG, missed=first)
        summary = scan(CONFIG, self.store, provider, NOW, True)
        departure = provider.batches[0].pairs()[0][0]
        # A date without an answer is not stored; the dashboard keeps the last known price.
        self.assertNotIn(("FRA", departure, (date.fromisoformat(departure) + timedelta(days=14)).isoformat(), "any"),
                         self.calendar(summary["run_id"]))
        self.assertIsNotNone(self.store.get_meta(gaps_key(CONFIG.scope("demo"))))
        # Not asked again in the same run: one batch per route and trip length.
        self.assertEqual(len(provider.batches), 12)
        later = FlakyProvider(CONFIG)
        summary = scan(CONFIG, self.store, later, NOW + timedelta(hours=6), True)
        catch_up = later.batches[0]
        self.assertEqual((catch_up.origin, catch_up.profile, catch_up.duration, [dep for dep, _ in catch_up.pairs()]),
                         ("FRA", "any", 14, [departure]))
        # The regular batch leaves that date out: every date is asked once per run.
        regular = [b for b in later.batches[1:] if (b.origin, b.profile, b.duration) == ("FRA", "any", 14)]
        self.assertEqual([len(b.pairs()) for b in regular], [1])
        self.assertEqual(sum(len(b.pairs()) for b in later.batches), summary["calendar_slots"])
        self.assertEqual(summary["calendar_slots"], 24)
        self.assertEqual((summary["calendar_dates_recovered"], summary["status"]), (1, "ok"))
        self.assertIsNone(self.store.get_meta(gaps_key(CONFIG.scope("demo"))))

    def test_an_airport_left_out_is_searched_first_next_time(self):
        scan(CONFIG, self.store, FlakyProvider(CONFIG, failing=("AMS",)), NOW, True)
        later = FlakyProvider(CONFIG)
        scan(CONFIG, self.store, later, NOW + timedelta(hours=6), True)
        self.assertEqual(later.asked[:4], ["AMS"] * 4)
        self.assertEqual(len(later.batches), 12)

    def test_unanswered_dates_of_an_old_search_window_are_dropped(self):
        scan(CONFIG, self.store, FlakyProvider(CONFIG, failing=("AMS",)), NOW, True)
        moved = replace(CONFIG, departure_end="2026-10-17")
        later = FlakyProvider(moved)
        scan(moved, self.store, later, NOW + timedelta(hours=6), True)
        self.assertNotEqual(later.asked[0], "AMS")


    def test_flight_checks_spread_over_departure_days(self):
        item = lambda origin, day, length=17: (origin, f"2026-11-{day:02d}", f"2026-11-{day + length:02d}", "any")
        # Cheapest first: four candidates leave on the 23rd, then other days.
        ranked = [item("AMS", 23), item("FRA", 23), item("DUS", 23), item("AMS", 23, 18), item("AMS", 7),
                  item("FRA", 11), item("AMS", 17), item("FRA", 7)]
        self.assertEqual(spread_checks([], ranked, 6), [item("AMS", 23), item("FRA", 23), item("AMS", 7),
                                                        item("FRA", 11), item("AMS", 17), item("FRA", 7)])
        # Checks left over still go to the next candidates, wherever they leave.
        self.assertEqual(spread_checks([], ranked, 8)[-2:], [item("DUS", 23), item("AMS", 23, 18)])
        # Watched dates stay first and count towards their day.
        watched = [item("DUS", 23)]
        self.assertEqual(spread_checks(watched, ranked, 4), [item("DUS", 23), item("AMS", 23), item("AMS", 7), item("FRA", 11)])
        # A short window allows more checks per day.
        self.assertEqual(len(spread_checks([], [item("AMS", 5, n) for n in range(10, 20)], 6)), 6)


if __name__ == "__main__":
    unittest.main()

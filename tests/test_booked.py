"""A booked trip: no more searches or price alerts, one message, stored prices stay."""
from dataclasses import replace
from datetime import date, timedelta
import tempfile
import unittest
from unittest.mock import Mock

from scripts import search_settings as settings
from scripts.build_site import public_config
from tracker.config import Config, Settings
from tracker.discord import payload_for
from tracker.planner import request_estimate
from tracker.provider import DemoProvider
from tracker.store import Store
from test_tracker import NOW, scan


class BookedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.addCleanup(self.store.close)
        self.config = Config()

    def test_a_booked_trip_is_not_searched_and_says_so_once(self):
        searching = scan(self.config, self.store, DemoProvider(self.config), NOW)
        scope = self.config.scope()
        pending = self.store.enqueue(searching["run_id"], "trend", NOW, "BKK price alert\nold", [], scope)
        booked = replace(self.config, booked="2026-09-18")
        provider = DemoProvider(booked)
        provider.fetch, provider.verify = Mock(), Mock()
        summary = scan(booked, self.store, provider, NOW + timedelta(hours=6))
        provider.fetch.assert_not_called()
        provider.verify.assert_not_called()
        self.assertEqual((summary["status"], summary["incomplete"], summary["queued_booked_notice"]), ("booked", False, 1))
        # Its undelivered price alert is of no use any more; the stored prices stay.
        self.assertEqual(self.store.db.execute("SELECT status FROM outbox WHERE id=?", (pending,)).fetchone()[0], "expired")
        self.assertTrue(self.store.db.execute("SELECT 1 FROM quotes WHERE scope=?", (scope,)).fetchone())
        notices = [row[0] for row in self.store.db.execute("SELECT text FROM outbox WHERE kind='notice'")]
        self.assertEqual(len(notices), 1)
        self.assertTrue(notices[0].startswith("BKK trip booked\n"))
        self.assertIn("marked as booked on 2026-09-18", notices[0])
        # The next run records it again, without a second message.
        self.assertEqual(scan(booked, self.store, DemoProvider(booked), NOW + timedelta(hours=12))["queued_booked_notice"], 0)

    def test_the_setting_is_a_date_and_costs_no_requests(self):
        for bad in ("yes", "2026-13-01", True):
            with self.assertRaises(ValueError):
                replace(self.config, booked=bad)
        booked = replace(self.config, booked="2026-09-18")
        self.assertEqual(booked.scope(), self.config.scope())  # Booking keeps the price history.
        self.assertEqual(request_estimate(booked, date(2026, 9, 18))["requests"], 0)
        self.assertNotIn("booked", self.config.file_dict())
        self.assertEqual(public_config(booked)["booked"], "2026-09-18")

    def test_booking_is_applied_even_after_the_travel_window(self):
        old = replace(self.config, departure_start="2026-09-01", departure_end="2026-09-05")
        with self.assertRaises(settings.Rejected):
            settings.check(old, date(2026, 9, 18))
        settings.check(replace(old, booked="2026-09-18"), date(2026, 9, 18))
        before = Settings((self.config,), "main")
        after = Settings((replace(self.config, booked="2026-09-18"),), "main")
        self.assertEqual(settings.changed_trips(before, after), ("main",))
        self.assertEqual(settings.shown("booked", "2026-09-18"), "yes, on 2026-09-18")

    def test_discord_title(self):
        text = ("BKK trip booked\n2026-09-18T12:00:00+00:00\nThe flight was marked as booked on 2026-09-18.\n"
                "To search again: https://example.github.io/flight-tracker/settings.html")
        embed = payload_for(text)["embeds"][0]
        self.assertEqual(embed["title"], "🎉 Bangkok · Trip booked")
        self.assertIn("[Search again →](https://example.github.io/flight-tracker/settings.html)", embed["description"])


if __name__ == "__main__":
    unittest.main()

"""Several destinations per trip: each is searched on its own, with its own history."""
from dataclasses import replace
from datetime import date, datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from scripts import search_settings as settings
from scripts.build_site import page_values, trip_data
from tracker.config import Config, Settings
from tracker.notifications import deliver, message_labels
from tracker.planner import request_estimate
from tracker.provider import DemoProvider, Quote
from tracker.service import scan_trips
from tracker.store import Store
from test_tracker import NOW

ROOT = Path(__file__).resolve().parents[1]
BKK = Config.load(ROOT / "config.json")
THAILAND = replace(BKK, more_destinations=("HKT", "CNX"))


class DestinationTests(unittest.TestCase):
    def test_destinations_are_checked_and_the_first_keeps_the_history(self):
        for more in (("BKK",), ("HKT", "HKT"), ("FRA",), ("hkt",), ("HKT", "CNX", "USM", "KBV", "DPS"), ["HKT"]):
            with self.subTest(more=more), self.assertRaises(ValueError):
                replace(BKK, more_destinations=more)
        self.assertEqual(THAILAND.destinations, ("BKK", "HKT", "CNX"))
        searches = THAILAND.searches()
        self.assertEqual([s.destination for s in searches], ["BKK", "HKT", "CNX"])
        self.assertEqual(searches[0], BKK)
        self.assertEqual(THAILAND.scope(), BKK.scope())
        self.assertEqual(len({s.scope() for s in searches}), 3)
        self.assertEqual(Config.from_dict(THAILAND.file_dict()), THAILAND)
        self.assertNotIn("more_destinations", BKK.file_dict())
        self.assertEqual(len(Settings((THAILAND,)).searches()), 3)

    def test_every_destination_costs_its_own_requests(self):
        today = date(2026, 10, 1)
        one, three = request_estimate(BKK, today), request_estimate(THAILAND, today)
        self.assertEqual(three["calendar"], 3 * one["calendar"])
        self.assertEqual(three["verification"], 3 * one["verification"])

    def test_each_destination_is_searched_and_alerted_on_its_own(self):
        with tempfile.TemporaryDirectory() as directory, Store(directory, "demo") as store:
            summaries = scan_trips(THAILAND.searches(), store, lambda trip: DemoProvider(trip), NOW, demo=True)
            self.assertEqual(len(summaries), 3)
            scopes = {row[0] for row in store.db.execute("SELECT DISTINCT scope FROM quotes")}
            self.assertEqual(scopes, {s.scope("demo") for s in THAILAND.searches()})
        with tempfile.TemporaryDirectory() as directory, Store(directory) as store:
            later = datetime(2026, 10, 24, tzinfo=timezone.utc)
            scan_trips(THAILAND.searches(), store, lambda trip: DemoProvider(trip), later)
            notices = [row[0] for row in store.db.execute("SELECT text FROM outbox WHERE kind='notice'")]
            # One notice for the trip, naming all of its destinations.
            self.assertEqual(len(notices), 1)
            self.assertTrue(notices[0].startswith("BKK search window ended\n"))
            self.assertIn("to BKK, HKT, CNX can no longer be searched", notices[0])

    def test_delivery_keeps_the_messages_of_every_destination(self):
        with tempfile.TemporaryDirectory() as directory, Store(directory) as store:
            phuket = THAILAND.searches()[1]
            store.db.execute("INSERT INTO runs VALUES('r',?,?,'ok','{}')", (NOW.isoformat(), phuket.scope()))
            quote = Quote("DUS", "2026-10-20", "2026-11-03", "layover", 60000, 900, 950, 1, 1, "TG", "")
            store.enqueue("r", "trend", NOW, "HKT price alert\nx", [quote], phuket.scope())
            sent = []
            sender = type("Sender", (), {"send": lambda self, text, **_: sent.append(text) or "1"})()
            deliver(store, Settings((THAILAND,)), NOW, sender)
            self.assertEqual(sent, ["HKT price alert\nx"])
        self.assertEqual(message_labels(Settings((THAILAND,)))[0], None)  # Messages name their own destination.
        self.assertEqual(message_labels(Settings((BKK,)))[0], "BKK")

    def test_the_dashboard_shows_all_destinations_of_a_trip_together(self):
        with tempfile.TemporaryDirectory() as directory:
            with Store(directory) as store:
                for search in THAILAND.searches():
                    store.db.execute("INSERT INTO runs VALUES(?,?,?,'ok',?)", (search.destination, NOW.isoformat(), search.scope(),
                        json.dumps(dict(calendar_queries_ok=4, calendar_queries_planned=4, verified_quotes=1, errors=[]))))
                    quote = DemoProvider(search).verify("DUS", "2026-10-20", "2026-11-03", "any")[0]
                    store.db.execute("INSERT INTO quotes VALUES(?,?,?,?,?,?,?,?,?)", (search.destination, search.scope(), NOW.isoformat(),
                        quote.origin, quote.departure, quote.return_date, quote.category, quote.price, json.dumps(quote.to_dict())))
                store.db.commit()
            trip = trip_data(Path(directory) / "history.sqlite3", THAILAND, NOW)
        self.assertEqual(sorted(offer["destination"] for offer in trip["offers"]), ["BKK", "CNX", "HKT"])
        self.assertEqual(len(trip["histories"]), 3)
        self.assertEqual(trip["config"]["destinations"], ["BKK", "HKT", "CNX"])
        self.assertEqual((trip["scan"]["batches_ok"], trip["scan"]["status"]), (12, "ok"))
        self.assertIn("HKT", trip["places"])
        page = page_values(THAILAND)
        self.assertEqual((page["city"], page["route_codes"], page["code"]), ("Bangkok, Phuket & Chiang Mai", "BKK · HKT · CNX", "BKK"))
        self.assertEqual(page["ticket_place"], "THAILAND / BKK · HKT · CNX")

    def test_settings_name_destinations_and_keep_histories_when_adding_or_reordering(self):
        self.assertEqual(settings.changes(BKK, THAILAND), [("More destinations", "none", "HKT, CNX")])
        self.assertEqual(settings.route(THAILAND), "DUS, FRA, AMS → BKK, HKT, CNX")
        self.assertFalse(settings.restarts(BKK, THAILAND))
        self.assertFalse(settings.restarts(THAILAND, replace(THAILAND, destination="HKT", more_destinations=("BKK",))))
        self.assertTrue(settings.restarts(THAILAND, replace(THAILAND, travel_class="business")))
        self.assertTrue(settings.restarts(BKK, replace(BKK, destination="HKT")))
        with self.assertRaisesRegex(settings.Rejected, "XQZ"):
            settings.check(replace(BKK, more_destinations=("XQZ",)), date(2026, 10, 1))


if __name__ == "__main__":
    unittest.main()

"""Open-jaw trips: out to the destination, back from another airport (multi-city search)."""
from dataclasses import replace
from datetime import date, timedelta
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from scripts import search_settings as settings
from scripts.build_site import export_data, page_values
from tracker.alerts import search_link
from tracker.config import Config
from tracker.planner import Batch
from tracker.provider import FreeProvider, Quote, normalize_pairs
from tracker.store import Store, stamp
from tracker.trends import block
from test_flight_times import direction, leg
from test_tracker import NOW

ROOT = Path(__file__).resolve().parents[1]
BKK = Config.load(ROOT / "config.json")
PHUKET_BACK = replace(BKK, return_from="HKT")


def trip(back_from):
    return (direction([leg("FRA", "BKK", "2026-10-15T13:25", "2026-10-16T05:40")], 675),
            direction([leg(back_from, "FRA", "2026-10-29T00:05", "2026-10-29T06:35")], 750, price=700))


class OpenJawTests(unittest.TestCase):
    def test_return_airport_is_checked_and_starts_its_own_history(self):
        for value in ("BKK", "FRA", "hkt", "", 5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                replace(BKK, return_from=value)
        self.assertNotEqual(PHUKET_BACK.scope(), BKK.scope())
        self.assertNotIn("return_from", BKK.file_dict())
        self.assertEqual(Config.from_dict(PHUKET_BACK.file_dict()), PHUKET_BACK)
        # A destination equal to the return airport is an ordinary round trip.
        both = replace(PHUKET_BACK, more_destinations=("HKT",))
        self.assertEqual([(s.destination, s.return_from) for s in both.searches()], [("BKK", "HKT"), ("HKT", None)])
        self.assertEqual(both.searches()[1].scope(), replace(BKK, destination="HKT").scope())

    def test_google_gets_a_multi_city_search(self):
        provider = FreeProvider.__new__(FreeProvider)
        provider.config = PHUKET_BACK
        out, back = ((date.today() + timedelta(days=d)).isoformat() for d in (30, 44))
        params = provider.common("FRA", out, back, "any")
        self.assertEqual(params["trip_type"].name, "MULTI_CITY")
        first, second = params["flight_segments"]
        self.assertEqual((first.departure_airport[0][0].name, first.arrival_airport[0][0].name, first.travel_date), ("FRA", "BKK", out))
        self.assertEqual((second.departure_airport[0][0].name, second.arrival_airport[0][0].name, second.travel_date), ("HKT", "FRA", back))
        provider.config = BKK
        self.assertEqual(provider.common("FRA", out, back, "any")["trip_type"].name, "ROUND_TRIP")
        # The date search builds its filters from the same segments.
        provider.config = PHUKET_BACK
        provider.dates = NS(search=Mock(return_value=[]))
        start = date.today() + timedelta(days=30)
        provider.fetch(Batch("FRA", "any", start, start, 14))
        self.assertEqual(provider.dates.search.call_args.args[0].trip_type.name, "MULTI_CITY")

    def test_only_itineraries_back_from_the_return_airport_count(self):
        normalize = lambda pair, config: normalize_pairs([pair], "FRA", "2026-10-15", "2026-10-29", "any", config, lambda _: "")
        quote, = normalize(trip("HKT"), PHUKET_BACK)
        self.assertEqual((quote.price, quote.category), (70000, "nonstop"))
        self.assertEqual(normalize(trip("BKK"), PHUKET_BACK), [])
        self.assertEqual(normalize(trip("HKT"), BKK), [])

    def test_links_messages_and_dashboard_name_the_return_airport(self):
        quote = Quote("FRA", "2026-10-20", "2026-11-03", "nonstop", 70000, 675, 750, 0, 0, "TG", "")
        self.assertIn("Multi-city+flights+FRA+to+BKK+2026-10-20%2C+HKT+to+FRA+2026-11-03", search_link(quote, "BKK", return_from="HKT"))
        history = {"previous": None, "low": None, "count": 0}
        self.assertIn("FIRST PRICE | FRA-BKK, back HKT-FRA | NON-STOP", block(quote, history, None, PHUKET_BACK))
        self.assertEqual(page_values(PHUKET_BACK)["trip_kind"], "Back from Phuket (HKT)")
        self.assertEqual(page_values(BKK)["trip_kind"], "Round trip")
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            store.db.execute("INSERT INTO runs VALUES('r',?,?,'ok','{}')", (stamp(NOW), PHUKET_BACK.scope()))
            store.db.execute("INSERT INTO quotes VALUES('r',?,?,?,?,?,?,?,?)", (PHUKET_BACK.scope(), stamp(NOW), quote.origin,
                             quote.departure, quote.return_date, quote.category, quote.price, json.dumps(quote.to_dict())))
            store.db.commit()
            data = export_data(Path(temp) / "history.sqlite3", PHUKET_BACK, NOW)
        offer, = data["offers"]
        self.assertEqual(offer["return_from"], "HKT")
        self.assertIn("Multi-city", offer["link"])
        self.assertEqual(data["config"]["return_from"], "HKT")

    def test_settings_name_the_return_airport_and_restart_its_history(self):
        self.assertEqual(settings.changes(BKK, PHUKET_BACK), [("Return flight from", "the destination", "HKT")])
        self.assertEqual(settings.route(PHUKET_BACK), "DUS, FRA, AMS → BKK (back from HKT)")
        self.assertTrue(settings.restarts(BKK, PHUKET_BACK))
        with self.assertRaisesRegex(settings.Rejected, "XQZ"):
            settings.check(replace(BKK, return_from="XQZ"), date(2026, 10, 1))


if __name__ == "__main__":
    unittest.main()

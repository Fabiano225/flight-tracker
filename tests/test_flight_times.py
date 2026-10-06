"""Local departure and arrival times of checked flights, for the dashboard."""
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest

from scripts.build_site import export_data
from tracker.config import Config
from tracker.provider import DemoProvider, Quote, normalize_pairs
from tracker.store import Store, stamp
from test_tracker import NOW


def leg(src, dst, departs, arrives):
    return NS(departure_airport=NS(name=src), arrival_airport=NS(name=dst), airline=NS(name="QR"),
              departure_datetime=datetime.fromisoformat(departs), arrival_datetime=datetime.fromisoformat(arrives))


def direction(legs, minutes, price=500):
    return NS(legs=legs, duration=minutes, stops=len(legs) - 1, currency="EUR", price=price, self_transfer=False)


def flights():
    """Frankfurt–Doha–Bangkok and back, arriving on the next day both ways."""
    return (direction([leg("FRA", "DOH", "2026-10-15T16:35", "2026-10-15T23:55"),
                       leg("DOH", "BKK", "2026-10-16T01:50", "2026-10-16T12:40")], 1085),
            direction([leg("BKK", "DOH", "2026-10-29T20:05", "2026-10-29T23:30"),
                       leg("DOH", "FRA", "2026-10-30T01:35", "2026-10-30T06:25")], 920, price=640))


class FlightTimeTests(unittest.TestCase):
    def normalize(self, pairs):
        return normalize_pairs(pairs, "FRA", "2026-10-15", "2026-10-29", "any", Config(), lambda _: "")

    def test_first_departure_and_last_arrival_of_each_direction_are_kept(self):
        quote, = self.normalize([flights()])
        self.assertEqual(quote.schedule, ("2026-10-15T16:35", "2026-10-16T12:40", "2026-10-29T20:05", "2026-10-30T06:25"))
        self.assertEqual(Quote(**json.loads(json.dumps(quote.to_dict()))).schedule, list(quote.schedule))

    def test_a_source_without_arrival_times_still_gives_the_price(self):
        pair = flights()
        for direction_ in pair:
            for leg_ in direction_.legs:
                del leg_.arrival_datetime
        quote, = self.normalize([pair])
        self.assertIsNone(quote.schedule)
        self.assertEqual(quote.price, 64000)

    def test_demo_quotes_have_times_on_their_travel_dates(self):
        quote, = DemoProvider(Config()).verify("FRA", "2026-10-15", "2026-10-29", "any")
        self.assertEqual([t[:10] for t in quote.schedule[::2]], ["2026-10-15", "2026-10-29"])

    def test_only_well_formed_times_on_the_travel_dates_are_published(self):
        config = Config()
        base = Quote("FRA", "2026-10-15", "2026-10-29", "layover", 60000, 900, 950, 1, 1, "QR", "")
        good = ["2026-10-15T16:35", "2026-10-16T12:40", "2026-10-29T20:05", "2026-10-30T06:25"]
        cases = [(good, good), (None, None), (good[:3], None), ([*good[:3], "06:25"], None),
                 (["2026-10-16T16:35", *good[1:]], None), ([*good[:3], "2026-10-30T25:00"], None),
                 ([*good[:3], 625], None), ("2026-10-15T16:35", None)]
        for stored, published in cases:
            with self.subTest(stored=stored), tempfile.TemporaryDirectory() as temp, Store(temp) as store:
                quote = replace(base, schedule=stored)
                store.db.execute("INSERT INTO runs VALUES('r',?,?,'ok','{}')", (stamp(NOW), config.scope()))
                store.db.execute("INSERT INTO quotes VALUES('r',?,?,?,?,?,?,?,?)",
                                 (config.scope(), stamp(NOW), quote.origin, quote.departure, quote.return_date,
                                  quote.category, quote.price, json.dumps(quote.to_dict())))
                store.db.commit()
                offer, = export_data(Path(temp) / "history.sqlite3", config, NOW)["offers"]
                self.assertEqual(offer["schedule"], published)


if __name__ == "__main__":
    unittest.main()

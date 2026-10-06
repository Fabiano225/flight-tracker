"""Several travellers: Google prices the whole party, the tracker keeps per-person prices."""
from datetime import date, datetime, timedelta
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from scripts.build_site import export_data, page_values
from tracker.alerts import search_link
from tracker.config import Config, per_person
from tracker.fare_baggage import booking_quotes
from tracker.planner import Batch
from tracker.provider import FreeProvider, Quote, normalize_pairs
from tracker.store import Store
from tracker.trends import block
from test_flight_times import flights
from test_tracker import NOW

TWO = Config(adults=2)


class TravellerTests(unittest.TestCase):
    def test_one_to_nine_adults_each_with_their_own_history(self):
        self.assertEqual(Config(adults=9).adults, 9)
        for adults in (0, 10, True, 1.0):
            with self.subTest(adults=adults), self.assertRaises(ValueError):
                Config(adults=adults)
        self.assertNotEqual(TWO.scope(), Config().scope())
        self.assertEqual(Config(adults=1).scope(), Config().scope())

    def test_party_totals_become_per_person_prices(self):
        self.assertEqual(per_person(130001, 2), 65001)  # Half a cent rounds up.
        self.assertEqual(per_person(130000, 3), 43333)
        self.assertEqual(per_person(65025, 1), 65025)
        provider = FreeProvider.__new__(FreeProvider)
        provider.config = TWO
        start = date.today() + timedelta(days=30)
        batch = Batch("FRA", "any", start, start, 14)
        dep, ret = batch.pairs()[0]
        provider.dates = NS(search=Mock(return_value=[NS(date=(datetime.fromisoformat(dep), datetime.fromisoformat(ret)),
                                                          price=1300.50, currency="EUR")]))
        self.assertEqual(provider.fetch(batch)[(dep, ret)], 65025)
        self.assertEqual(provider.dates.search.call_args.args[0].passenger_info.adults, 2)
        quote, = normalize_pairs([flights()], "FRA", "2026-10-15", "2026-10-29", "any", TWO, lambda _: "")
        self.assertEqual(quote.price, 32000)  # The return-selection price of 640 EUR covers both.

    def test_booking_offers_are_per_person_too(self):
        captured = []
        search = NS(client=None, get_booking_options=lambda *args, **kwargs: captured.append(args))
        http = NS(post=Mock())
        quote = Quote("FRA", "2026-10-15", "2026-10-29", "layover", 32000, 900, 950, 1, 1, "QR", "")
        with patch("tracker.fare_baggage.decode_booking_quotes", return_value=[]) as decode:
            search.get_booking_options = lambda *args, **kwargs: search.client.post("url", data="x")
            booking_quotes(search, http, flights(), NS(passenger_info=NS(adults=2)), quote)
        self.assertEqual(decode.call_args.args[3], 2)

    def test_links_messages_and_page_name_the_party(self):
        quote = Quote("FRA", "2026-10-15", "2026-10-29", "layover", 60000, 900, 950, 1, 1, "QR", "")
        self.assertIn("one+adult", search_link(quote, "BKK"))
        self.assertIn("2+adults", search_link(quote, "BKK", adults=2))
        history = {"previous": None, "low": None, "count": 0}
        self.assertIn("2026-10-15 to 2026-10-29 | 600.00 EUR per person, 1200.00 EUR for 2", block(quote, history, None, TWO))
        self.assertIn("2026-10-15 to 2026-10-29 | 600.00 EUR\n", block(quote, history, None, Config()))
        self.assertEqual(page_values(TWO)["travellers"], "2 adults")
        self.assertEqual(page_values(Config())["travellers"], "1 adult")
        with tempfile.TemporaryDirectory() as temp:
            Store(temp).close()
            self.assertEqual(export_data(Path(temp) / "history.sqlite3", TWO, NOW)["config"]["adults"], 2)


if __name__ == "__main__":
    unittest.main()

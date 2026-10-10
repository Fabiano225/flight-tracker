"""A trip's own currency and search country, e.g. Thai baht searched from Thailand."""
from dataclasses import replace
from datetime import date, timedelta
import json
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from scripts.build_site import page_values, public_config
from scripts import search_settings as settings
from tracker.alerts import search_link
from tracker.config import Config, Settings, money
from tracker.planner import Batch
from tracker.network import ServiceError
from tracker.provider import FreeProvider, Quote, normalize_pairs
from tracker.report import report
from tracker.store import Store, stamp
from tracker.trends import block
from test_flight_times import flights
from test_tracker import NOW

THAI = Config(currency="THB", country="TH", good_deal_nonstop_eur=26000, good_deal_layover_eur=24500,
              drop_eur=2000, realert_improvement_eur=1000)


def in_baht(pair):
    for direction in pair:
        direction.currency = "THB"
    pair[1].price = 24320
    return pair


class CurrencyTests(unittest.TestCase):
    def test_currency_and_country_are_checked_and_start_their_own_history(self):
        self.assertEqual((THAI.currency, THAI.country), ("THB", "TH"))
        for values in ({"currency": "XYZ"}, {"currency": "thb"}, {"country": "XX"}, {"country": "th"}, {"country": None}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                Config(**values)
        scopes = {Config().scope(), replace(Config(), currency="THB").scope(), replace(Config(), country="TH").scope()}
        self.assertEqual(len(scopes), 3)
        # Searching from Germany stays the default: existing histories keep their scope.
        self.assertEqual(replace(Config(), country="DE").scope(), Config().scope())
        self.assertNotIn("country", Config().file_dict())
        self.assertEqual(THAI.file_dict()["country"], "TH")
        self.assertEqual(Settings.from_dict(THAI.file_dict()).primary, THAI)

    def test_money_in_messages(self):
        self.assertEqual(money(61600), "616.00 EUR")
        self.assertEqual(money(2432000, "THB"), "24,320.00 THB")
        self.assertEqual(money(-150000, "THB", sign=True), "-1,500.00 THB")
        self.assertEqual(money(9800000, "JPY"), "98,000 JPY")

    def test_google_is_searched_in_the_trip_s_currency_and_country(self):
        provider = FreeProvider(THAI)
        self.addCleanup(provider.close)
        start = date.today() + timedelta(days=30)
        provider.flights.search = Mock(return_value=[])
        provider.verify("FRA", start.isoformat(), (start + timedelta(days=14)).isoformat(), "any")
        self.assertEqual({k: provider.flights.search.call_args.kwargs[k] for k in ("currency", "country", "language")},
                         {"currency": "THB", "country": "TH", "language": "en"})
        provider.dates = Mock(search=Mock(return_value=[]))
        provider.fetch(Batch("FRA", "any", start, start, 14))
        self.assertEqual(provider.dates.search.call_args.kwargs, {"currency": "THB", "country": "TH", "language": "en"})

    def test_only_prices_in_the_trip_s_currency_count(self):
        normalize = lambda pair, config: normalize_pairs([pair], "FRA", "2026-10-15", "2026-10-29", "any", config, lambda _: "")
        quote, = normalize(in_baht(flights()), THAI)
        self.assertEqual(quote.price, 2432000)
        self.assertEqual(normalize(flights(), THAI), [])
        self.assertEqual(normalize(in_baht(flights()), Config()), [])
        # Date searches keep baht prices; one answered in another currency is rejected,
        # not mixed into the history.
        provider = FreeProvider(THAI)
        self.addCleanup(provider.close)
        start = date.today() + timedelta(days=30)
        provider.dates.shopping._fetch_flights = Mock(return_value=[NS(price=24320, currency="THB")])
        self.assertEqual(list(provider.fetch(Batch("FRA", "any", start, start, 14)).values()), [2432000])
        provider.dates.shopping._fetch_flights = Mock(return_value=[NS(price=600, currency="EUR")])
        with self.assertRaisesRegex(ServiceError, "differs from THB"):
            provider.fetch(Batch("FRA", "any", start, start, 14))

    def test_links_messages_reports_and_website_use_the_currency(self):
        quote = Quote("FRA", "2026-10-15", "2026-10-29", "layover", 2432000, 1085, 920, 1, 1, "QR", "")
        link = search_link(quote, THAI)
        self.assertIn("curr=THB", link)
        self.assertIn("gl=TH", link)
        self.assertIn("gl=DE", search_link(quote, Config()))
        history = {"previous": 2582000, "previous_at": stamp(NOW), "low": 2582000, "count": 4, "first_at": stamp(NOW)}
        text = block(quote, history, None, THAI)
        self.assertIn("2026-10-15 to 2026-10-29 | 24,320.00 THB", text)
        self.assertIn("25,820.00 THB -> 24,320.00 THB (-1,500.00 THB / -5.8%)", text)
        self.assertIn("CHECK TO BUY: within your 24,500.00 THB target.", text)
        values = page_values(THAI)
        self.assertEqual((values["budget"], values["realert"]), ("Separate targets", "฿1,000"))
        self.assertEqual(page_values(replace(THAI, good_deal_nonstop_eur=24500))["budget"], "฿24,500")
        self.assertEqual({k: public_config(THAI)[k] for k in ("currency", "country", "currency_symbol")},
                         {"currency": "THB", "country": "TH", "currency_symbol": "฿"})
        self.assertIn(("Currency", "Euro (EUR)", "Thai baht (THB)"), settings.trip_changes(Config(), THAI))
        self.assertIn(("Search from", "Germany", "Thailand"), settings.trip_changes(Config(), THAI))
        self.assertIn(("Price target, with stops", "€650", "฿24,500"), settings.trip_changes(Config(), THAI))
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            summary = dict(run_id="r", started=stamp(NOW), mode="live", trip="main", status="ok", config=THAI.public_dict(),
                           calendar_queries_ok=1, calendar_queries_planned=1, calendar_prices=1, calendar_slots=1,
                           verification_searches=1, verified_quotes=1, http_attempts=2, queued_deals=0, errors=[])
            store.db.execute("INSERT INTO runs VALUES('r',?,?,'ok',?)", (stamp(NOW), THAI.scope(), json.dumps(summary)))
            text = report(store)
        self.assertIn("THB round-trip prices per person. Searched as from Thailand.", text)
        self.assertIn("| Origin | Departure | Return | Type | THB |", text)


if __name__ == "__main__":
    unittest.main()

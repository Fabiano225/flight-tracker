"""Flight time windows: Google searches only flights leaving or landing inside them."""
from dataclasses import replace
from datetime import date, timedelta
import json
from pathlib import Path
import unittest

from scripts import search_settings as settings
from scripts.build_site import page_values
from tracker.config import Config, Settings
from tracker.provider import FreeProvider, normalize_pairs
from test_flight_times import flights

ROOT = Path(__file__).resolve().parents[1]
BKK = Config.load(ROOT / "config.json")
# Flights of flights(): out 16:35 → 12:40 (+1), back 20:05 → 06:25 (+1).
DAYTIME = replace(BKK, flight_times={"outbound_departure": [8, 22], "return_arrival": [6, 24]})


class FlightWindowTests(unittest.TestCase):
    def normalize(self, config):
        return normalize_pairs([flights()], "FRA", "2026-10-15", "2026-10-29", "any", config, lambda _: "")

    def test_windows_are_checked_and_start_their_own_history(self):
        for times in ({"outbound_departure": [22, 8]}, {"outbound_departure": [0, 24]}, {"outbound_departure": [6, 25]},
                      {"outbound_departure": [6]}, {"outbound_departure": [6.5, 20]}, {"lunch": [12, 13]}, ["06-22"]):
            with self.subTest(times=times), self.assertRaises(ValueError):
                replace(BKK, flight_times=times)
        self.assertNotEqual(DAYTIME.scope(), BKK.scope())
        self.assertNotIn("flight_times", BKK.file_dict())
        self.assertEqual(Config.from_dict(DAYTIME.file_dict()), DAYTIME)

    def test_only_flights_inside_the_exact_windows_count(self):
        self.assertEqual(len(self.normalize(DAYTIME)), 1)
        self.assertEqual(self.normalize(replace(DAYTIME, flight_times={"outbound_departure": [6, 16]})), [])
        # The return lands at 06:25: before 07:00, not before 06:00.
        self.assertEqual(len(self.normalize(replace(BKK, flight_times={"return_arrival": [0, 7]}))), 1)
        self.assertEqual(self.normalize(replace(BKK, flight_times={"return_arrival": [0, 6]})), [])
        self.assertFalse(DAYTIME.times_fit(None))  # Unknown times never pass a limited search.
        self.assertTrue(BKK.times_fit(None))

    def test_google_gets_each_direction_its_own_widened_window(self):
        provider = FreeProvider.__new__(FreeProvider)
        provider.config = replace(BKK, flight_times={"outbound_departure": [8, 22], "return_arrival": [0, 7]})
        start = (date.today() + timedelta(days=30)).isoformat()
        back = (date.today() + timedelta(days=44)).isoformat()
        outbound, inbound = provider.common("FRA", start, back, "any")["flight_segments"]
        self.assertEqual(outbound.time_restrictions.model_dump(),
                         {"earliest_departure": 8, "latest_departure": 22, "earliest_arrival": None, "latest_arrival": None})
        self.assertEqual(inbound.time_restrictions.model_dump(),
                         {"earliest_departure": None, "latest_departure": None, "earliest_arrival": None, "latest_arrival": 7})
        provider.config = BKK
        self.assertIsNone(provider.common("FRA", start, back, "any")["flight_segments"][0].time_restrictions)

    def test_settings_keep_a_fixed_order_and_name_the_windows(self):
        values = {**BKK.form_dict(), "flight_times": {"return_arrival": [6, 24], "outbound_departure": [8, 22]}}
        config = settings.normalize(Config.from_dict(values))
        self.assertEqual(json.dumps(config.flight_times), json.dumps(DAYTIME.flight_times))
        self.assertEqual(settings.changes(BKK, config), [
            ("Flight times", "any time", "outbound departs 08:00–22:00; return lands 06:00–24:00")])
        self.assertEqual(Settings.from_dict(config.file_dict()).primary, config)
        self.assertEqual(page_values(DAYTIME)["filter_summary"], " · limited flight times")


if __name__ == "__main__":
    unittest.main()

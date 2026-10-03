"""Price targets per departure airport: alerts, settings and the dashboard export."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from scripts import search_settings as settings
from scripts.build_site import export_data, page_values
from tracker.config import Config, Settings
from tracker.provider import Quote
from tracker.store import Store
from tracker.trends import block
from test_tracker import NOW

ROOT = Path(__file__).resolve().parents[1]
BKK = Config.load(ROOT / "config.json")
TARGETS = {"DUS": {"layover": 700}, "AMS": {"nonstop": 820, "layover": 500}}


class TargetTests(unittest.TestCase):
    def test_each_airport_uses_its_own_target_or_the_trips(self):
        config = replace(BKK, origin_targets=TARGETS)
        self.assertEqual(config.threshold("layover", "DUS"), 70000)
        self.assertEqual(config.threshold("nonstop", "DUS"), 65000)  # Not set for DUS: the trip's.
        self.assertEqual(config.threshold("layover", "FRA"), 65000)
        self.assertEqual(config.threshold("nonstop", "AMS"), 82000)
        self.assertEqual(config.threshold("layover"), 65000)
        # Price targets never change which prices are comparable.
        self.assertEqual(config.scope(), BKK.scope())

    def test_alert_messages_judge_a_flight_by_its_airports_target(self):
        config = replace(BKK, origin_targets=TARGETS)
        history = {"previous": None, "low": None, "count": 0}
        quote = Quote("DUS", "2026-10-20", "2026-11-03", "layover", 68000, 900, 950, 1, 1, "QR", "")
        self.assertIn("CHECK TO BUY: within your 700.00 EUR target.", block(quote, history, None, config))
        self.assertIn("WATCH: 30.00 EUR above your target.", block(replace(quote, origin="FRA"), history, None, config))

    def test_invalid_airport_targets_are_rejected(self):
        for targets in ({"MUC": {"layover": 500}}, {"AMS": {}}, {"AMS": {"business": 500}}, {"AMS": {"layover": 0}},
                        {"AMS": {"layover": True}}, ["AMS"]):
            with self.subTest(targets=targets), self.assertRaises(ValueError):
                replace(BKK, origin_targets=targets)

    def test_settings_store_only_targets_that_differ_in_a_fixed_order(self):
        values = {**BKK.form_dict(), "origin_targets": {"AMS": {"layover": 500, "nonstop": 820}, "FRA": {"layover": 650},
                                                        "DUS": {"layover": 700}}}
        config = settings.normalize(Config.from_dict(values))
        self.assertEqual(json.dumps(config.origin_targets), json.dumps({"DUS": {"layover": 700},
                                                                         "AMS": {"nonstop": 820, "layover": 500}}))
        self.assertEqual(Settings.from_dict(config.file_dict()).primary, config)
        self.assertNotIn("origin_targets", BKK.file_dict())
        self.assertEqual(settings.changes(BKK, config), [
            ("Price targets per airport", "same for all airports",
             "AMS: non-stop €820, with stops €500; DUS: with stops €700")])

    def test_the_dashboard_gets_the_airport_targets(self):
        config = replace(BKK, origin_targets=TARGETS)
        self.assertEqual(page_values(config)["budget"], "Per airport")
        with tempfile.TemporaryDirectory() as temp:
            Store(temp).close()
            data = export_data(Path(temp) / "history.sqlite3", config, NOW)
        self.assertEqual(data["config"]["origin_targets"], TARGETS)


if __name__ == "__main__":
    unittest.main()

"""A latest return date as an alternative to the longest trip length."""
from dataclasses import replace
from datetime import date
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from scripts import search_settings as settings
from scripts.build_site import page_values
from tracker.config import Config
from tracker.planner import plan, request_estimate
from tracker.provider import Quote
from tracker.store import Store
from tracker.trends import load_watches, watch_key
from test_tracker import NOW

ROOT = Path(__file__).resolve().parents[1]
BKK = Config.load(ROOT / "config.json")  # Departures 20–23 Oct, 14–21 days.
BACK_BY = replace(BKK, max_trip_days=19, latest_return="2026-11-08")


class ReturnDateTests(unittest.TestCase):
    def test_only_date_pairs_that_return_in_time_are_searched(self):
        pairs = {pair for batch in plan(BACK_BY, date(2026, 10, 1)) for pair in batch.pairs()}
        self.assertEqual(max(ret for _, ret in pairs), "2026-11-08")
        # Earlier departures can stay longer: 6 lengths from 20 Oct, 3 from 23 Oct.
        self.assertEqual(sorted({dep for dep, _ in pairs}), ["2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"])
        self.assertEqual(sum(dep == "2026-10-20" for dep, _ in pairs), 6)
        self.assertEqual(sum(dep == "2026-10-23" for dep, _ in pairs), 3)
        self.assertEqual(request_estimate(BACK_BY, date(2026, 10, 1))["calendar"], 18 * 3 * 2)
        self.assertTrue(all(BACK_BY.fits(dep, ret) for dep, ret in pairs))
        self.assertFalse(BACK_BY.fits("2026-10-20", "2026-11-09"))
        self.assertTrue(BKK.fits("2026-10-20", "2026-11-09"))

    def test_invalid_return_dates_are_rejected(self):
        for value in ("2026-11-02", "2026/11/08", "20261108", 20261108):
            with self.subTest(value=value), self.assertRaises(ValueError):
                replace(BKK, latest_return=value)

    def test_existing_searches_keep_their_file_scope_and_watches(self):
        self.assertIsNone(BKK.latest_return)
        self.assertEqual(BKK.file_dict(), json.loads((ROOT / "config.json").read_text(encoding="utf-8")))
        self.assertEqual(BKK.scope(), BACK_BY.scope())
        self.assertEqual(watch_key(BKK, "s"), watch_key(replace(BKK, latest_return=None), "s"))
        self.assertNotEqual(watch_key(BKK, "s"), watch_key(replace(BKK, latest_return="2026-11-13"), "s"))
        self.assertEqual(BACK_BY.file_dict()["latest_return"], "2026-11-08")

    def test_watches_and_pending_alerts_outside_the_return_date_drop_out(self):
        late = Quote("FRA", "2026-10-20", "2026-11-10", "layover", 60000, 900, 950, 1, 1, "QR", "")
        with tempfile.TemporaryDirectory() as temp, Store(temp) as store:
            store.set_meta(watch_key(BACK_BY, BACK_BY.scope()), json.dumps({"FRA:layover": late.to_dict()}))
            self.assertEqual(load_watches(store, BACK_BY, BACK_BY.scope(), NOW), {})
            store.db.execute("INSERT INTO runs VALUES('r',?,?,'ok','{}')", (NOW.isoformat(), BACK_BY.scope()))
            store.enqueue("r", "trend", NOW, "BKK price alert\nx", [late], BACK_BY.scope())
            store.expire_outside_search((BACK_BY,))
            self.assertEqual(store.db.execute("SELECT status FROM outbox").fetchone()[0], "expired")

    def test_texts_and_change_table_name_the_return_date(self):
        self.assertEqual(page_values(BACK_BY)["trip_days"], "14+ days · back by 8 Nov")
        self.assertEqual(settings.changes(BKK, BACK_BY), [("Longest trip (days)", "21", "19"),
                                                          ("Latest return", "none", "2026-11-08")])
        self.assertIn("back by 2026-11-08", settings.summary(BACK_BY))

    @unittest.skipUnless(shutil.which('node'), 'node not installed')
    def test_form_estimate_matches_the_planner(self):
        for config in (BKK, BACK_BY, replace(BACK_BY, latest_return="2026-11-03"), replace(BKK, max_trip_days=30,
                                                                                          latest_return="2026-11-30")):
            values = {**config.form_dict(), "origins": list(config.origins)}
            script = ("import {requestEstimate} from './website/search-model.mjs';"
                      f"console.log(JSON.stringify(requestEstimate({json.dumps(values)},'2026-10-01')));")
            result = json.loads(subprocess.run(["node", "--input-type=module", "-e", script], cwd=ROOT,
                                               capture_output=True, text=True, check=True).stdout)
            expected = request_estimate(config, date(2026, 10, 1))
            self.assertEqual({k: result[k] for k in expected}, expected, config.latest_return)


if __name__ == "__main__":
    unittest.main()

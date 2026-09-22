import csv
import tempfile
import unittest

from tracker.config import Config
from tracker.provider import DemoProvider
from tracker.report import report
from tracker.service import scan
from tracker.store import Store
from test_tracker import NOW


class ReportTests(unittest.TestCase):
    def test_extended_quotes_keep_csv_schema_and_generate_report(self):
        config=Config()
        with tempfile.TemporaryDirectory() as temp, Store(temp,'demo') as store:
            scan(config,store,DemoProvider(config),NOW,True)
            text=report(store)
            self.assertIn('synthetic test data',text)
            with (store.directory/'latest.csv').open(encoding='utf8',newline='') as f:
                rows=list(csv.DictReader(f))
            self.assertTrue(rows)
            self.assertIn('price_eur',rows[0])
            self.assertNotIn('itinerary_id',rows[0])
            self.assertTrue((store.directory/'latest.json').exists())
            self.assertTrue((store.directory/'report.md').exists())

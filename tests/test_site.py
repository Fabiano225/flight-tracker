from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from scripts.build_site import export_data, build, ASSETS
from tracker.config import Config
from tracker.provider import Quote
from tracker.store import Store, stamp

NOW = datetime(2026,9,20,12,tzinfo=timezone.utc)


class SiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.store=Store(self.root/'state')
        self.config=Config()
        self.path=self.root/'state'/'history.sqlite3'
        self.q=Quote('FRA','2026-10-15','2026-10-29','layover',60000,900,950,1,1,'TG','https://evil.example/token')

    def tearDown(self):
        self.store.close();self.temp.cleanup()

    def record(self, name, at, price=None, status='ok', quote=None, scope=None):
        scope=scope or self.config.scope()
        summary=dict(calendar_queries_ok=48,calendar_queries_planned=48,verified_quotes=int(price is not None),
                     errors=['sensitive-error-value'] if status=='partial' else [],config={'secret':'not-public'})
        self.store.db.execute('INSERT INTO runs VALUES(?,?,?,?,?)',(name,stamp(at),scope,status,json.dumps(summary)))
        if price is not None:
            q=replace(quote or self.q,price=price)
            self.store.db.execute('INSERT INTO quotes VALUES(?,?,?,?,?,?,?,?,?)',
                (name,scope,stamp(at),q.origin,q.departure,q.return_date,q.category,q.price,json.dumps(q.to_dict())))
        self.store.db.commit()

    def data(self):
        return export_data(self.path,self.config,NOW)

    def test_current_offers_and_real_history(self):
        self.record('old',NOW-timedelta(hours=6),65000)
        self.record('new',NOW,60000)
        data=self.data();self.assertEqual(len(data['offers']),1)
        item=data['offers'][0]
        self.assertEqual(item['price'],60000)
        self.assertEqual([p['price'] for p in data['histories'][item['id']]],[65000,60000])
        self.assertEqual(data['scan']['at'],data['offers_as_of'])
        self.assertEqual(item['days'],14)

    def test_failed_scan_retains_old_data_with_distinct_timestamp(self):
        self.record('old',NOW-timedelta(hours=6),60000)
        self.record('new',NOW,None,'partial')
        data=self.data();self.assertEqual(data['scan']['status'],'partial')
        self.assertNotEqual(data['scan']['at'],data['offers_as_of'])
        self.assertEqual(data['scan']['issues'],1)

    def test_no_outbox_metadata_raw_errors_or_untrusted_links_exported(self):
        self.record('new',NOW,60000,'partial')
        self.store.set_meta('TELEGRAM_BOT_TOKEN','secret-token-value')
        self.store.enqueue('new','health',NOW,'private-message-value')
        self.store.db.commit()
        raw=json.dumps(self.data())
        for forbidden in ['secret-token-value','private-message-value','sensitive-error-value','evil.example','not-public','message_id','chat_id','outbox']:
            self.assertNotIn(forbidden,raw)
        self.assertTrue(self.data()['offers'][0]['link'].startswith('https://www.google.com/travel/flights?'))

    def test_demo_rejected(self):
        self.store.set_meta('mode','demo');self.store.db.commit()
        with self.assertRaisesRegex(ValueError,'Only live'):self.data()

    def test_scope_dates_duration_and_history_window_filtered(self):
        self.record('old',NOW-timedelta(days=31),50000)
        self.record('wrongscope',NOW,50000,scope='wrong')
        self.record('wrongdate',NOW,50000,quote=replace(self.q,departure='2026-10-13'))
        self.record('wrongduration',NOW,50000,quote=replace(self.q,return_date='2026-11-12'))
        self.record('longflight',NOW,50000,quote=replace(self.q,outbound_minutes=1260))
        self.record('new',NOW,60000)
        data=self.data();self.assertEqual(len(data['offers']),1)
        self.assertEqual(len(next(iter(data['histories'].values()))),1)

    def test_october_14_departure_is_published(self):
        self.record('new', NOW, 60000, quote=replace(self.q, departure='2026-10-14'))
        data = self.data()
        self.assertEqual(data['config']['departure_start'], '2026-10-14')
        self.assertEqual(len(data['offers']), 1)
        self.assertEqual(data['offers'][0]['days'], 15)

    def test_empty_history_does_not_make_up_prices(self):
        data=self.data();self.assertEqual(data['offers'],[]);self.assertEqual(data['histories'],{})
        self.assertIsNone(data['scan'])

    def test_build_only_publishes_allowlisted_assets(self):
        self.record('new',NOW,60000)
        output=self.root/'public'
        build(self.path,output)
        self.assertEqual({p.name for p in output.iterdir()},set(ASSETS)|{'data.json'})
        with self.assertRaisesRegex(ValueError,'empty'):build(self.path,output)


if __name__=='__main__':unittest.main()

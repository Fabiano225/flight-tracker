from dataclasses import replace
from datetime import timedelta
import json
import tempfile
from types import SimpleNamespace
import unittest

from tracker.baggage import scan_baggage
from tracker.config import Config
from tracker.network import BudgetError
from tracker.provider import Quote
from tracker.store import Store, stamp
from scripts.build_site import export_data
from test_tracker import NOW


class BaggageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(self.temp.name)
        self.config=Config()
        self.q=Quote('FRA','2026-10-14','2026-10-28','layover',55000,900,950,1,1,'WY','https://evil.example/private', 'a'*64)
        self.base('base',NOW)
        self.calls=[]
        self.closed=False
        self.provider=SimpleNamespace(config=self.config,http=SimpleNamespace(used=0),verify=self.verify,close=self.close)

    def tearDown(self):
        self.store.close();self.temp.cleanup()

    def base(self,run,now):
        self.store.db.execute('INSERT INTO runs VALUES(?,?,?,?,?)',(run,stamp(now),self.config.scope(),'ok','{}'))
        self.store.db.execute('INSERT INTO quotes VALUES(?,?,?,?,?,?,?,?,?)',
            (run,self.config.scope(),stamp(now),self.q.origin,self.q.departure,self.q.return_date,self.q.category,self.q.price,json.dumps(self.q.to_dict())))
        self.store.db.commit()

    def verify(self,*args):
        c=self.provider.config
        self.calls.append((c.carry_on_bags,c.checked_bags,args))
        self.provider.http.used+=1
        return [replace(self.q,price=self.q.price+1000*c.carry_on_bags+5000*c.checked_bags)]

    def close(self):self.closed=True

    def export(self,variant,now=NOW):
        return export_data(self.store.directory/'history.sqlite3',self.config,now,variant)

    def test_three_profiles_never_touch_base_quotes_or_telegram(self):
        before=list(self.store.db.execute('SELECT * FROM quotes'))
        result=scan_baggage(self.config,self.store,self.provider,NOW)
        self.assertEqual(result['status'],'ok')
        self.assertEqual([x[:2] for x in self.calls],[(1,0),(0,1),(1,1)])
        self.assertEqual(list(self.store.db.execute('SELECT * FROM quotes')),before)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0],0)
        self.assertTrue(self.closed)
        self.assertEqual(self.export('cabin')['offers'][0]['price'],56000)
        self.assertEqual(self.export('checked')['offers'][0]['price'],60000)
        self.assertEqual(self.export('both')['offers'][0]['price'],61000)

    def test_allowlist_unknown_weights_and_independent_histories(self):
        scan_baggage(self.config,self.store,self.provider,NOW)
        data=self.export('both')
        q=data['offers'][0]
        self.assertIsNone(q['baggage']['carry_on_kg'])
        self.assertIsNone(q['baggage']['checked_kg'])
        self.assertFalse(q['baggage']['allowance_confirmed'])
        self.assertNotIn('evil.example',json.dumps(data))
        self.assertTrue(q['id'].startswith('both:'))
        self.assertEqual(data['histories'][q['id']],[{'at':stamp(NOW),'price':61000}])
        self.assertEqual(data['base_at'],stamp(NOW))

    def test_budget_error_leaves_honest_partial_results_without_base_fallback(self):
        scan_baggage(self.config,self.store,self.provider,NOW)
        later=NOW+timedelta(hours=6)
        self.base('next',later)
        def fail(*args):raise BudgetError('Stop')
        self.provider.verify=fail
        result=scan_baggage(self.config,self.store,self.provider,later)
        self.assertEqual(result['status'],'partial')
        data=self.export('both',later)
        self.assertEqual(data['offers'],[])
        self.assertEqual(data['scan']['status'],'partial')
        self.assertTrue(data['histories'])  # history retained, not presented as current

    def test_retry_replaces_snapshot_without_duplicate_history(self):
        scan_baggage(self.config,self.store,self.provider,NOW)
        scan_baggage(self.config,self.store,self.provider,NOW+timedelta(minutes=5))
        data=self.export('both',NOW+timedelta(minutes=5))
        self.assertEqual(len(next(iter(data['histories'].values()))),1)

    def test_old_or_other_scope_base_is_not_searched(self):
        self.assertEqual(scan_baggage(self.config,self.store,self.provider,NOW+timedelta(days=1))['status'],'skipped')
        changed=replace(self.config,checked_bags=1)
        self.assertEqual(scan_baggage(changed,self.store,self.provider,NOW)['status'],'skipped')
        self.assertEqual(self.calls,[])

    def test_export_before_first_baggage_scan_and_invalid_profile(self):
        self.assertEqual(self.export('cabin')['offers'],[])
        with self.assertRaises(ValueError):self.export('unknown')

    def test_invalid_duration_or_scope_filtered_from_public_baggage(self):
        self.provider.verify=lambda *args:[replace(self.q,outbound_minutes=1260)]
        scan_baggage(self.config,self.store,self.provider,NOW)
        self.assertEqual(self.export('both')['offers'],[])
        changed=replace(self.config,checked_bags=1)
        self.assertEqual(export_data(self.store.directory/'history.sqlite3',changed,NOW,'both')['offers'],[])

if __name__=='__main__':unittest.main()

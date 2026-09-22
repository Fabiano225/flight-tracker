from dataclasses import replace
from datetime import timedelta
import json
import tempfile
import unittest

from tracker.config import Config
from tracker.provider import Quote, DemoProvider
from tracker.service import scan
from tracker.store import Store, stamp
from tracker.trends import queue_trends, load_watches, watch_key, watch_searches
from test_tracker import NOW


class TrendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.config = Config()
        self.scope = self.config.scope()
        self.index = 0
        self.quote = Quote('FRA', '2026-10-15', '2026-10-29', 'layover',
                           60000, 990, 1050, 1, 1, 'Test Airline', '')

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def run_quotes(self, quotes):
        self.index += 1
        now = NOW + timedelta(hours=6 * self.index)
        run = str(self.index)
        self.store.db.execute("INSERT INTO runs VALUES(?,?,?,'ok','{}')", (run, stamp(now), self.scope))
        verified = {(q.origin, q.departure, q.return_date, q.category): q for q in quotes}
        count = queue_trends(self.store, self.config, self.scope, run, verified, now)
        for q in quotes:
            self.store.db.execute('INSERT INTO quotes VALUES(?,?,?,?,?,?,?,?,?)',
                (run, self.scope, stamp(now), q.origin, q.departure, q.return_date, q.category, q.price,
                 json.dumps(q.to_dict())))
        texts = [r[0] for r in self.store.db.execute('SELECT text FROM outbox WHERE run_id=?', (run,))]
        self.store.db.execute("UPDATE outbox SET status='sent' WHERE status='pending'")
        return count, '\n'.join(texts)

    def test_stable_prices_and_rotating_dates_stay_silent(self):
        self.assertEqual(self.run_quotes([self.quote])[0], 1)
        for ret in ('2026-10-30', '2026-10-31', '2026-11-01'):
            self.assertEqual(self.run_quotes([self.quote, replace(self.quote, return_date=ret)])[0], 0)

    def test_rise_and_fall_show_euros_percent_and_previous_observation(self):
        self.run_quotes([self.quote])
        count, text = self.run_quotes([replace(self.quote, price=55000)])
        self.assertEqual(count, 1)
        self.assertIn('PREIS GESUNKEN', text)
        self.assertIn('600.00 EUR -> 550.00 EUR (-50.00 EUR / -8.3%)', text)
        self.assertIn('Letzte Messung gleicher Daten', text)
        self.assertIn('Bisheriges 30-Tage-Tief: 600.00 EUR', text)
        count, text = self.run_quotes([replace(self.quote, price=70000)])
        self.assertEqual(count, 1)
        self.assertIn('PREIS GESTIEGEN', text)
        self.assertIn('550.00 EUR -> 700.00 EUR (+150.00 EUR / +27.3%)', text)
        self.assertIn('BEOBACHTEN', text)

    def test_small_changes_accumulate_since_last_alert(self):
        self.run_quotes([self.quote])
        self.assertEqual(self.run_quotes([replace(self.quote, price=61000)])[0], 0)
        self.assertEqual(self.run_quotes([replace(self.quote, price=62000)])[0], 0)
        count, text = self.run_quotes([replace(self.quote, price=62500)])
        self.assertEqual(count, 1)
        self.assertIn('600.00 EUR -> 625.00 EUR', text)
        self.assertIn('620.00 EUR -> 625.00 EUR', text)

    def test_crossing_budget_alerts_even_below_25_euros(self):
        self.run_quotes([replace(self.quote, price=65500)])
        count, text = self.run_quotes([replace(self.quote, price=65000)])
        self.assertEqual(count, 1)
        self.assertIn('KAUF PRUEFEN', text)
        count, text = self.run_quotes([replace(self.quote, price=65100)])
        self.assertEqual(count, 1)
        self.assertIn('BEOBACHTEN', text)

    def test_new_dates_are_not_misrepresented_as_same_trip_drop(self):
        self.run_quotes([self.quote])
        count, text = self.run_quotes([self.quote, replace(self.quote, return_date='2026-10-30', price=57000)])
        self.assertEqual(count, 1)
        self.assertIn('GUENSTIGERE ALTERNATIVE', text)
        self.assertIn('Vorherige Daten: 2026-10-15 bis 2026-10-29', text)
        self.assertNotIn('PREIS GESUNKEN', text)

    def test_missing_watch_not_reported_as_price_rise(self):
        self.run_quotes([self.quote])
        self.assertEqual(self.run_quotes([])[0], 0)
        self.assertEqual(self.run_quotes([replace(self.quote, return_date='2026-10-30', price=80000)])[0], 0)
        watches = load_watches(self.store, self.config, self.scope, NOW)
        self.assertEqual(watches['FRA:layover']['return_date'], self.quote.return_date)

    def test_alternative_does_not_hide_rise(self):
        self.run_quotes([self.quote])
        count, text = self.run_quotes([replace(self.quote, price=80000),
                                      replace(self.quote, return_date='2026-10-30', price=70000)])
        self.assertEqual(count, 1)
        self.assertIn('PREIS GESTIEGEN', text)

    def test_categories_stay_separate_and_initial_above_budget_is_labelled(self):
        direct = replace(self.quote, category='nonstop', price=90000)
        count, text = self.run_quotes([self.quote, direct])
        self.assertEqual(count, 2)
        self.assertIn('STARTWERT', text)
        self.assertIn('Noch kein Preisverlauf', text)
        self.assertIn('BEOBACHTEN: 250.00 EUR', text)
        count, text = self.run_quotes([replace(self.quote, price=55000), direct])
        self.assertEqual(count, 1)
        self.assertNotIn('DIREKT (beide Richtungen)', text)

    def test_strong_drop_and_history_window(self):
        self.run_quotes([replace(self.quote, price=100000)])
        count, text = self.run_quotes([replace(self.quote, price=90000)])
        self.assertIn('STARKER DEAL', text)
        self.assertIn('BEOBACHTEN', text)
        self.index = 150  # Prior quote history is older than 30 days.
        count, text = self.run_quotes([replace(self.quote, price=80000)])
        self.assertIn('Noch kein Preisverlauf', text)
        self.assertNotIn('STARKER DEAL', text)

    def test_within_budget_but_above_low_is_not_buy_signal(self):
        self.run_quotes([replace(self.quote, price=50000)])
        count, text = self.run_quotes([self.quote])
        self.assertEqual(count, 1)
        self.assertIn('IM BUDGET, aber 100.00 EUR', text)
        self.assertNotIn('KAUF PRUEFEN', text)

    def test_watch_and_notification_baseline_survive_reopen(self):
        self.run_quotes([self.quote])
        self.store.close()
        self.store = Store(self.temp.name)
        self.assertTrue(load_watches(self.store, self.config, self.scope, NOW))
        self.assertEqual(self.run_quotes([self.quote])[0], 0)

    def test_pending_dedup_and_expiry(self):
        self.run_quotes([self.quote])
        self.store.db.execute("UPDATE outbox SET status='pending'")
        self.assertEqual(self.run_quotes([self.quote])[0], 0)
        self.store.db.execute("UPDATE outbox SET status='pending'")
        self.store.expire(NOW + timedelta(days=2), 12, self.scope)
        self.assertEqual(self.run_quotes([self.quote])[0], 1)

    def test_window_change_expires_pending_trends(self):
        self.run_quotes([self.quote])
        self.store.db.execute("UPDATE outbox SET status='pending'")
        config = replace(self.config, departure_start='2026-10-16')
        self.store.expire_outside_search(config)
        self.assertEqual(self.store.db.execute('SELECT status FROM outbox').fetchone()[0], 'expired')
        self.assertEqual(load_watches(self.store, config, self.scope, NOW), {})

    def test_expanded_window_preserves_notified_dates_and_checks_new_deals(self):
        self.config = replace(self.config, departure_start='2026-10-15')
        self.run_quotes([self.quote])
        self.config = replace(self.config, departure_start='2026-10-14')
        watches = load_watches(self.store, self.config, self.scope, NOW)
        self.assertEqual(watch_searches(watches), [('FRA', '2026-10-15', '2026-10-29', 'any')])
        earlier = replace(self.quote, departure='2026-10-14', return_date='2026-10-28')
        self.assertEqual(self.run_quotes([self.quote, earlier])[0], 0)
        count, text = self.run_quotes([self.quote, replace(earlier, price=57000)])
        self.assertEqual(count, 1)
        self.assertIn('GUENSTIGERE ALTERNATIVE', text)
        self.assertEqual(load_watches(self.store, self.config, self.scope, NOW)['FRA:layover']['departure'],
                         '2026-10-14')

    def test_repairs_already_saved_unannounced_date_switch(self):
        self.run_quotes([self.quote])
        wrong = replace(self.quote, departure='2026-10-14', return_date='2026-10-28')
        self.store.set_meta(watch_key(self.config, self.scope), json.dumps({'FRA:layover': wrong.to_dict()}))
        self.assertEqual(load_watches(self.store, self.config, self.scope, NOW)['FRA:layover'],
                         self.quote.to_dict())

    def test_window_recovery_never_imports_another_scope_or_removed_airport(self):
        self.run_quotes([self.quote])
        changed = replace(self.config, checked_bags=1)
        self.assertEqual(load_watches(self.store, changed, changed.scope(), NOW), {})
        changed = replace(self.config, origins=('AMS',))
        self.assertEqual(load_watches(self.store, changed, self.scope, NOW), {})

    def test_telegram_length_limit_and_all_six_groups(self):
        quotes = [replace(self.quote, origin=origin, category=category)
                  for origin in self.config.origins for category in ('nonstop', 'layover')]
        self.assertEqual(self.run_quotes(quotes)[0], 6)
        self.assertEqual(self.run_quotes([replace(q, price=55000) for q in quotes])[0], 6)
        for row in self.store.db.execute('SELECT text FROM outbox'):
            self.assertLessEqual(len(row[0]), 4096)


class WatchVerificationTests(unittest.TestCase):
    def test_watched_dates_verified_first_even_after_rise(self):
        config = replace(Config(), max_verifications_per_run=6)
        with tempfile.TemporaryDirectory() as temp, Store(temp, 'demo') as store:
            scan(config, store, DemoProvider(config, 200), NOW, True)
            watches = load_watches(store, config, config.scope('demo'), NOW)
            provider = DemoProvider(config, -100)
            calls = []
            original = provider.verify

            def verify(*args):
                calls.append(args)
                return original(*args)

            provider.verify = verify
            scan(config, store, provider, NOW + timedelta(hours=6), True)
            self.assertEqual(len(calls), 6)
            self.assertEqual({(v['origin'], v['departure'], v['return_date']) for v in watches.values()},
                             {c[:3] for c in calls})


if __name__ == '__main__':
    unittest.main()

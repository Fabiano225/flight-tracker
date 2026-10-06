"""Several trips per run: separate histories and alerts, one request budget."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from tracker.config import SHARED_FIELDS, Config, Settings
from tracker.discord import payload_for
from tracker.network import ServiceError
from tracker.notifications import deliver, message_labels
from tracker.provider import DemoProvider, GuardedClient, Quote, cache_file
from tracker.report import report
from tracker.service import scan_trips
from tracker.store import Store, stamp
from test_tracker import NOW

ROOT = Path(__file__).resolve().parents[1]
SHARED = set(SHARED_FIELDS)
BKK = Config.load(ROOT / 'config.json')
AMS = replace(BKK, id='ams', origins=('FRA',), destination='AMS', min_trip_days=3, max_trip_days=4,
              good_deal_nonstop_eur=120, good_deal_layover_eur=100)


def two_trips(**shared):
    trips = (replace(BKK, **shared), replace(AMS, **shared))
    return Settings(trips, 'main')


def multi_file(**changes):
    return {'primary_trip': 'main', 'trips': [
        {'id': 'main', **{k: v for k, v in BKK.file_dict().items() if k not in SHARED}},
        {'id': 'ams', 'origins': ['FRA'], 'destination': 'AMS', **{k: v for k, v in BKK.file_dict().items()
                                                                  if k not in SHARED | {'origins', 'destination'}}},
    ], **{k: BKK.file_dict()[k] for k in SHARED}, **changes}


class SettingsTests(unittest.TestCase):
    def test_original_config_is_the_main_trip_with_its_history(self):
        settings = Settings.load(ROOT / 'config.json')
        self.assertEqual(settings.primary, BKK)
        self.assertEqual(settings.primary.id, 'main')
        # The Bangkok price history keeps its scope, and the file keeps its layout.
        self.assertEqual(settings.primary.scope(), '3a24f282d466fb0085f6b791')
        self.assertEqual(settings.file_dict(), json.loads((ROOT / 'config.json').read_text(encoding='utf-8')))

    def test_trips_round_trip_and_share_the_request_settings(self):
        values = multi_file(max_http_attempts_per_run=1800)
        settings = Settings.from_dict(values)
        self.assertEqual([t.id for t in settings.trips], ['main', 'ams'])
        self.assertTrue(all(t.max_http_attempts_per_run == 1800 for t in settings.trips))
        self.assertEqual(Settings.from_dict(settings.file_dict()), settings)
        self.assertEqual(list(settings.file_dict()), ['primary_trip', 'trips', *SHARED_FIELDS])
        self.assertNotIn('max_run_seconds', settings.file_dict()['trips'][1])
        self.assertEqual(settings.file_dict()['trips'][1]['id'], 'ams')

    def test_primary_trip_comes_first_and_can_change(self):
        settings = Settings.from_dict(multi_file(primary_trip='ams'))
        self.assertEqual(settings.primary.destination, 'AMS')
        self.assertEqual([t.id for t in settings.ordered()], ['ams', 'main'])
        self.assertEqual(settings.file_dict()['primary_trip'], 'ams')

    def test_every_trip_has_its_own_history(self):
        main, other = BKK, replace(BKK, id='bkk-2')
        self.assertNotEqual(main.scope(), other.scope())
        self.assertEqual(other.scope(), replace(other, good_deal_layover_eur=1).scope())
        self.assertNotEqual(other.scope(), replace(other, id='bkk-3').scope())

    def test_invalid_trip_lists_are_rejected(self):
        trip = multi_file()['trips'][1]
        cases = {
            'id must be': multi_file(trips=[{**trip, 'id': 'Has Space'}]),
            'unique': multi_file(trips=[trip, trip]),
            '1 to 5 trips': multi_file(trips=[]),
            'primary_trip must name': multi_file(primary_trip='nowhere'),
            'set it once': multi_file(trips=[{**trip, 'max_run_seconds': 600}]),
            'Unknown settings keys': multi_file(surprise=1),
            'needs an id': multi_file(trips=[{k: v for k, v in trip.items() if k != 'id'}]),
        }
        cases['1 to 5 trips (six)'] = multi_file(trips=[{**trip, 'id': f't{i}'} for i in range(6)])
        for message, values in cases.items():
            with self.subTest(message), self.assertRaisesRegex(ValueError, message.split(' (')[0]):
                Settings.from_dict(values)
        with self.assertRaisesRegex(ValueError, 'same request settings'):
            Settings((BKK, replace(AMS, max_run_seconds=600)))

    def test_each_trip_caches_searches_in_its_own_file(self):
        self.assertEqual(cache_file('state', BKK).name, 'search-cache-main-BKK.json')
        self.assertEqual(cache_file('state', AMS).name, 'search-cache-ams-AMS.json')


class SharedStats:
    used = 0


class ScanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name, 'demo')
        self.http = SharedStats()
        bkk = replace(BKK, departure_start='2026-10-14', departure_end='2026-10-15', min_trip_days=14, max_trip_days=14)
        ams = replace(AMS, departure_start='2026-10-14', departure_end='2026-10-15', min_trip_days=3, max_trip_days=3)
        self.settings = Settings((bkk, ams))
        self.trips = self.settings.ordered()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def provider(self, trip, fail=False):
        provider = DemoProvider(trip, 0)
        stats = self.http

        def fetch(batch):
            stats.used += 1
            if fail:
                raise ServiceError('Flight source HTTP 503')
            return DemoProvider.fetch(provider, batch)
        provider.http, provider.fetch = stats, fetch
        return provider

    def scan(self, at=NOW, failing=()):
        return scan_trips(self.trips, self.store, lambda trip: self.provider(trip, trip.id in failing), at, True)

    def pending(self, kind):
        return self.store.db.execute(
            "SELECT o.text FROM outbox o WHERE o.kind=? AND o.status='pending' ORDER BY o.rowid", (kind,)).fetchall()

    def test_trips_keep_their_own_runs_and_alerts(self):
        first, second = self.scan()
        scopes = {row[0] for row in self.store.db.execute('SELECT scope FROM runs')}
        self.assertEqual(scopes, {trip.scope('demo') for trip in self.trips})
        self.assertEqual((first['trip'], second['trip']), ('main', 'ams'))
        # The second trip's scan must not expire the first trip's alerts.
        alerts = [row[0].split('\n')[0] for row in self.pending('trend')]
        self.assertEqual(alerts, ['DEMO - synthetic prices', 'DEMO - synthetic prices'])
        destinations = {row[0] for row in self.store.db.execute(
            "SELECT DISTINCT a.scope FROM alert_items a JOIN outbox o ON o.id=a.outbox_id WHERE o.status='pending'")}
        self.assertEqual(destinations, scopes)
        # Requests are counted per trip on the shared client.
        self.assertEqual(first['http_attempts'] + second['http_attempts'], self.http.used)
        self.assertGreater(second['http_attempts'], 0)

    def test_check_status_of_one_trip_survives_the_next_trips_scan(self):
        self.scan()
        self.store.db.execute("UPDATE outbox SET status='sent', message_id='1' WHERE status='pending'")
        self.store.db.commit()
        self.scan(NOW + timedelta(hours=6))
        statuses = [row[0].split('\n')[0] for row in self.pending('check_status')]
        self.assertEqual(statuses, ['DEMO - synthetic check status'] * 2)
        # The next run replaces each trip's undelivered status with a newer one.
        self.scan(NOW + timedelta(hours=12))
        self.assertEqual(len(self.pending('check_status')), 2)

    def test_one_health_message_names_each_failing_trip(self):
        first, second = self.scan(failing=('main', 'ams'))
        self.assertEqual((first['status'], second['status']), ('partial', 'partial'))
        health = self.pending('health')
        self.assertEqual(len(health), 1)
        self.assertIn('\nBKK: ', health[0][0])
        self.assertIn('\nAMS: ', health[0][0])
        self.scan(NOW + timedelta(hours=6), failing=('ams',))
        self.assertEqual(len(self.pending('health')), 1)  # At most one per day.
        self.scan(NOW + timedelta(hours=12))
        recovered = [row[0] for row in self.pending('health') if 'recovered' in row[0]]
        self.assertEqual(len(recovered), 1)

    def test_cleanup_keeps_the_longest_comparison_period(self):
        self.trips = (self.trips[0], replace(self.trips[1], history_window_days=90))
        old = stamp(NOW - timedelta(days=60))
        self.store.db.execute('INSERT INTO runs VALUES(?,?,?,?,?)', ('old', old, 's', 'ok', '{}'))
        self.store.db.execute('INSERT INTO calendar VALUES(?,?,?,?,?,?,?,?)',
                              ('old', 's', old, 'FRA', '2026-10-14', '2026-10-17', 'any', 9000))
        self.store.db.commit()
        self.scan()
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM calendar WHERE run_id='old'").fetchone()[0], 1)

    def test_report_covers_every_trip(self):
        self.scan()
        text = report(self.store)
        self.assertIn('# Flight tracker · 2 trips', text)
        self.assertIn('## BKK flight tracker', text)
        self.assertIn('## AMS flight tracker', text)
        latest = json.loads((self.store.directory / 'latest.json').read_text(encoding='utf-8'))
        self.assertEqual([trip['trip'] for trip in latest['trips']], ['main', 'ams'])
        header = (self.store.directory / 'latest.csv').read_text(encoding='utf-8').splitlines()[0]
        self.assertTrue(header.startswith('trip,destination,origin,'))

    def test_ended_windows_are_announced_per_trip(self):
        twin = replace(self.trips[0], id='bkk-2')
        with tempfile.TemporaryDirectory() as directory, Store(directory) as store:
            later = datetime(2026, 10, 20, tzinfo=timezone.utc)
            scan_trips((self.trips[0], twin), store, lambda trip: DemoProvider(trip), later)
            notices = store.db.execute("SELECT COUNT(*) FROM outbox WHERE kind='notice'").fetchone()[0]
            self.assertEqual(notices, 2)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.settings = two_trips()
        self.q = {'main': Quote('DUS', '2026-10-20', '2026-11-03', 'layover', 60000, 900, 950, 1, 1, 'TG', ''),
                  'ams': Quote('FRA', '2026-10-20', '2026-10-23', 'nonstop', 9000, 70, 75, 0, 0, 'LH', '')}
        for trip in self.settings.trips:
            self.store.db.execute('INSERT INTO runs VALUES(?,?,?,?,?)', (trip.id, stamp(NOW), trip.scope(), 'ok', '{}'))
            self.store.enqueue(trip.id, 'trend', NOW, f'{trip.destination} price alert\nx', [self.q[trip.id]], trip.scope())
        self.store.db.commit()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_alerts_of_all_trips_are_delivered_in_order(self):
        sender = NS(send=Mock(side_effect=['1', '2']))
        self.assertEqual(deliver(self.store, self.settings, NOW, sender), 2)
        self.assertEqual([c.args[0][:3] for c in sender.send.call_args_list], ['BKK', 'AMS'])

    def test_alerts_of_a_removed_trip_are_not_delivered(self):
        sender = NS(send=Mock(return_value='1'))
        only_bangkok = Settings((self.settings.trip('main'),))
        self.assertEqual(deliver(self.store, only_bangkok, NOW, sender), 1)
        sender.send.assert_called_once_with('BKK price alert\nx')

    def test_messages_name_their_destination(self):
        self.assertEqual(message_labels(Settings((replace(BKK, display_names={'BKK': 'Krung Thep'}),))),
                         ('BKK', {'BKK': 'Krung Thep'}))
        code, names = message_labels(Settings((replace(BKK, display_names={'FRA': 'Frankfurt Main'}),
                                               replace(AMS, display_names={'FRA': 'Other', 'AMS': 'Schiphol'}))))
        self.assertIsNone(code)
        self.assertEqual(names, {'FRA': 'Frankfurt Main', 'AMS': 'Schiphol'})
        status = payload_for('AMS check status\n' + stamp(NOW) + '\nNo price change for the watched offers.',
                             names=names)
        self.assertEqual(status['embeds'][0]['title'], '✅ Schiphol · No price change')
        self.assertEqual(status['username'], 'AMS Flight Tracker')
        health = payload_for('Flight tracker needs attention.\nAMS: x', names=names)
        self.assertEqual((health['username'], health['embeds'][0]['title']),
                         ('Flight Tracker', '⚠️ Flight search needs attention'))


class SharedClientTests(unittest.TestCase):
    def test_providers_never_close_a_shared_client(self):
        try:
            from tracker.provider import FreeProvider
            import fli  # noqa: F401
        except ImportError:
            self.skipTest('flights package not installed')
        session = NS(close=Mock())
        http = GuardedClient(BKK, session=session, sleep=lambda _: None)
        for trip in (BKK, AMS):
            provider = FreeProvider(trip, http=http)
            self.assertIs(provider.http, http)
            provider.close()
        session.close.assert_not_called()
        http.close()
        session.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()

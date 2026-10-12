"""Searching only some trips: by hand, or the trips a settings change touched."""
from dataclasses import replace
from datetime import date, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tracker.provider import DemoProvider
from tracker.report import report
from tracker.service import scan_trips
from tracker.store import Store
from test_tracker import NOW
from test_trips import multi_file, two_trips

ROOT = Path(__file__).resolve().parents[1]


class OnlyTripsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = two_trips()

    def test_trip_ids_choose_searches_and_unknown_ids_fail(self):
        settings = self.settings
        self.assertEqual(settings.chosen(''), ())
        self.assertEqual(settings.chosen(' ams , main,ams '), ('ams', 'main'))
        self.assertEqual([s.id for s in settings.searches(('ams',))], ['ams'])
        self.assertEqual([s.id for s in settings.searches()], ['main', 'ams'])
        with self.assertRaisesRegex(ValueError, 'Unknown trip ID ber; the trips are main, ams'):
            settings.chosen('ber')

    def test_a_search_of_one_trip_leaves_the_others_alone(self):
        main, ams = self.settings.searches()
        with Store(self.temp.name, 'demo') as store:
            scan_trips((main, ams), store, lambda trip: DemoProvider(trip), NOW, True)
            first = {run['scope']: run['id'] for run in store.db.execute('SELECT * FROM runs')}
            # A message of the trip that is not searched now stays pending.
            returned = (date.fromisoformat(main.departure_start) + timedelta(days=main.min_trip_days)).isoformat()
            waiting = store.enqueue(first[main.scope('demo')], 'trend', NOW, 'main alert',
                                    [], main.scope('demo'))
            store.db.execute("INSERT INTO alert_items VALUES(?,?,?,?,?,?,?)",
                             (waiting, main.scope('demo'), 'DUS', main.departure_start, returned, 'layover', 60000))
            later = NOW + timedelta(hours=2)
            scan_trips((ams,), store, lambda trip: DemoProvider(trip), later, True, (main, ams))
            runs = store.db.execute('SELECT scope, COUNT(*) AS n FROM runs GROUP BY scope').fetchall()
            self.assertEqual({row['scope']: row['n'] for row in runs}, {main.scope('demo'): 1, ams.scope('demo'): 2})
            self.assertEqual(store.db.execute('SELECT status FROM outbox WHERE id=?', (waiting,)).fetchone()[0], 'pending')
            # The report still covers every trip, each with its latest search.
            report(store, (main, ams))
            latest = json.loads((Path(self.temp.name) / 'latest.json').read_text(encoding='utf-8'))
        self.assertEqual([(doc['trip'], doc['started']) for doc in latest['trips']],
                         [('main', NOW.isoformat(timespec='seconds')), ('ams', later.isoformat(timespec='seconds'))])

    def test_older_history_of_trips_not_searched_is_kept(self):
        long = two_trips()
        main = replace(long.trips[0], history_window_days=90)
        ams = long.trips[1]
        with Store(self.temp.name, 'demo') as store:
            scan_trips((main, ams), store, lambda trip: DemoProvider(trip), NOW - timedelta(days=60), True)
            scan_trips((ams,), store, lambda trip: DemoProvider(trip), NOW, True, (main, ams))
            # Date-search prices are kept as long as the longest comparison period needs them.
            kept = store.db.execute('SELECT COUNT(*) FROM calendar WHERE scope=?', (main.scope('demo'),)).fetchone()[0]
        self.assertGreater(kept, 0)

    def test_command_line_searches_only_the_named_trips(self):
        config = Path(self.temp.name) / 'config.json'
        config.write_text(json.dumps(multi_file()), encoding='utf-8')
        state = Path(self.temp.name) / 'state'
        run = lambda *args: subprocess.run([sys.executable, '-m', 'tracker', 'demo', '--config', str(config),
                                            '--state-dir', str(state), '--as-of', '2026-09-18', *args],
                                           cwd=ROOT, capture_output=True, text=True, env=os.environ.copy())
        result = run('--trips', 'ams')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('::notice::Searching only ams', result.stdout)
        with Store(state, 'demo') as store:
            trips = {json.loads(row[0])['trip'] for row in store.db.execute('SELECT summary FROM runs')}
        self.assertEqual(trips, {'ams'})
        result = run('--trips', 'ber')
        self.assertEqual(result.returncode, 1)
        self.assertIn('Unknown trip ID ber', result.stderr)


if __name__ == '__main__':
    unittest.main()

from datetime import date
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from scripts import search_settings as settings
from tracker.config import SHARED_FIELDS, Config, Settings
from tracker.places import place, places, supported
from tracker.planner import request_estimate

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 10, 1)


def body(values, marker=True):
    return ((settings.MARKER + '\n') if marker else '') + 'Neue Suche\n\n```json\n' + json.dumps(values, indent=2) + '\n```\n'


def git(*args, cwd):
    subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True)


class ConfigTests(unittest.TestCase):
    def test_config_file_round_trips_byte_for_byte(self):
        text = (ROOT / 'config.json').read_text(encoding='utf-8')
        self.assertEqual(settings.serialize(Config.load(ROOT / 'config.json')), text)

    def test_display_names_are_validated_and_not_part_of_history_scope(self):
        base = Config()
        named = Config(display_names={'BKK': 'Bangkok Suvarnabhumi'})
        self.assertEqual(base.scope(), named.scope())
        self.assertEqual(named.file_dict()['display_names'], {'BKK': 'Bangkok Suvarnabhumi'})
        self.assertNotIn('display_names', base.file_dict())
        for names in ({'bkk': 'x'}, {'BKK': ''}, {'BKK': ' padded'}, {'BKK': 'a\nb'}, {'BKK': 'x' * 41},
                      {'BKK': 'non breaking'}, {'BKK': 3}, ['BKK']):
            with self.assertRaises(ValueError, msg=names):
                Config(display_names=names)

    def test_from_dict_does_not_change_its_input(self):
        values = {'origins': ['FRA']}
        self.assertEqual(Config.from_dict(values).origins, ('FRA',))
        self.assertEqual(values, {'origins': ['FRA']})


class PlaceTests(unittest.TestCase):
    def test_german_names_and_fallbacks(self):
        self.assertEqual(place('BKK'), {'city': 'Bangkok', 'country': 'Thailand'})
        self.assertEqual(place('DUS')['city'], 'Düsseldorf')
        self.assertEqual(place('FRA')['city'], 'Frankfurt')
        self.assertEqual(place('HND', {'HND': 'Tokyo Haneda'}), {'city': 'Tokyo Haneda', 'country': 'Japan'})
        self.assertEqual(place('ZZZ'), {'city': 'ZZZ', 'country': ''})
        self.assertFalse(supported('ZZZ'))
        self.assertEqual(set(places(Config())), {'DUS', 'FRA', 'AMS', 'BKK'})

    def test_airport_list_matches_the_flight_source(self):
        try:
            from fli.models import Airport
        except ImportError:
            self.skipTest('flights package not installed')
        listed = set(json.loads((ROOT / 'tracker' / 'airports.json').read_text(encoding='utf-8'))['airports'])
        self.assertEqual(listed, {a.name for a in Airport})


class IssueTests(unittest.TestCase):
    def setUp(self):
        self.values = {**Config.load(ROOT / 'config.json').file_dict(), 'destination': 'HND',
                       'display_names': {'HND': 'Tokyo Haneda', 'BKK': 'stale', 'FRA': 'Frankfurt'}}

    def test_valid_settings_are_normalized(self):
        config = settings.normalize(settings.parse_issue(body(self.values)))
        self.assertEqual(config.destination, 'HND')
        # Automatic names and names for airports outside the route are dropped.
        self.assertEqual(config.display_names, {'HND': 'Tokyo Haneda'})
        self.assertEqual(settings.check(config, TODAY), request_estimate(config, TODAY))

    def test_rejections_explain_the_problem(self):
        cases = {
            'no search settings': body(self.values, marker=False),
            'settings block': settings.MARKER + '\nno JSON',
            'not valid JSON': settings.MARKER + '\n```json\n{oops\n```',
            'Settings are missing: destination': body({k: v for k, v in self.values.items() if k != 'destination'}),
            'Invalid setting': body({**self.values, 'max_trip_days': 200}),
            'Invalid setting: Unknown config keys': body({**self.values, 'surprise': 1}),
            'Invalid setting: Invalid max_stops': body({**self.values, 'max_stops': 5}),
        }
        for message, text in cases.items():
            with self.assertRaisesRegex(settings.Rejected, message):
                settings.parse_issue(text)
        too_long = settings.MARKER + 'x' * settings.MAX_BODY
        with self.assertRaises(settings.Rejected):
            settings.parse_issue(too_long)

    def test_unsupported_airport_past_window_and_budget_are_rejected(self):
        def check(**changes):
            settings.check(Config.from_dict({**self.values, **changes}), TODAY)
        with self.assertRaisesRegex(settings.Rejected, 'XQZ'):
            check(origins=['XQZ'])
        with self.assertRaisesRegex(settings.Rejected, 'no day from tomorrow on'):
            check(departure_start='2026-09-01', departure_end='2026-10-01')
        with self.assertRaisesRegex(settings.Rejected, 'Too many requests'):
            check(departure_end='2026-12-31')
        with self.assertRaisesRegex(settings.Rejected, 'Search too long'):
            check(request_interval_seconds=30)
        with self.assertRaisesRegex(settings.Rejected, 'airline FF'):
            check(airlines=['QR', 'FF'])
        # A non-stop-only search skips the any-stops profile and halves the date requests.
        self.assertEqual(settings.check(Config.from_dict({**self.values, 'max_stops': 0}), TODAY)['calendar'], 4 * 8 * 3)

    def test_change_table_uses_readable_labels(self):
        old = Config()
        new = Config(destination='HND', travel_class='business', hide_separate_tickets=False, max_stops=1,
                     airlines_exclude=('SU',))
        self.assertEqual(settings.changes(old, new), [
            ('Destination', 'BKK', 'HND'), ('Cabin', 'Economy', 'Business'),
            ('Hide separate tickets', 'yes', 'no'), ('Max. stops per direction', 'any', 'up to 1'),
            ('Exclude these airlines', 'none', 'SU')])
        self.assertEqual(settings.cell('a|b'), 'a\\|b')


def trips_values(**changes):
    """Bangkok (main) and Amsterdam as the multi-trip settings block of an issue."""
    base = Config.load(ROOT / 'config.json').file_dict()
    trip = {k: v for k, v in base.items() if k not in SHARED_FIELDS}
    amsterdam = {**trip, 'id': 'ams', 'origins': ['FRA'], 'destination': 'AMS', 'min_trip_days': 3,
                 'max_trip_days': 4, 'good_deal_nonstop_eur': 120, 'good_deal_layover_eur': 100}
    return {'primary_trip': 'main', 'trips': [{**trip, 'id': 'main'}, amsterdam],
            **{k: base[k] for k in SHARED_FIELDS}, **changes}


class TripIssueTests(unittest.TestCase):
    def test_all_trips_are_parsed_and_checked_together(self):
        new = settings.normalize(settings.parse_issue(body(trips_values())))
        self.assertIsInstance(new, Settings)
        self.assertEqual([t.destination for t in new.ordered()], ['BKK', 'AMS'])
        estimate = settings.check(new, TODAY)
        self.assertEqual(estimate['requests'], sum(request_estimate(t, TODAY)['requests'] for t in new.trips))

    def test_missing_trip_settings_are_named_per_trip(self):
        values = trips_values()
        del values['trips'][1]['destination'], values['max_run_seconds']
        with self.assertRaisesRegex(settings.Rejected, 'missing: max_run_seconds, trip 2: destination'):
            settings.parse_issue(body(values))
        with self.assertRaisesRegex(settings.Rejected, 'Choose 1 to 5 trips'):
            settings.parse_issue(body(trips_values(trips=[])))
        with self.assertRaisesRegex(settings.Rejected, 'Invalid setting: .*set it once'):
            values = trips_values()
            values['trips'][1]['max_run_seconds'] = 600
            settings.parse_issue(body(values))

    def test_budget_is_shared_and_ended_trips_must_go(self):
        long = trips_values()
        long['trips'][1].update(departure_end='2026-12-31', max_trip_days=11)
        # Each trip alone fits the budget of 1600 requests; together they do not.
        self.assertTrue(all(request_estimate(t, TODAY)['requests'] < 1600 for t in Settings.from_dict(long).trips))
        with self.assertRaisesRegex(settings.Rejected, 'Too many requests: about \\d+ per run for all trips together'):
            settings.check(Settings.from_dict(long), TODAY)
        ended = trips_values()
        ended['trips'][1].update(departure_start='2026-09-20', departure_end='2026-10-01')
        with self.assertRaisesRegex(settings.Rejected, 'AMS trip: The departure window .* remove this trip'):
            settings.check(Settings.from_dict(ended), TODAY)

    def test_change_table_names_trips(self):
        single = Settings.load(ROOT / 'config.json')
        both = Settings.from_dict(trips_values())
        self.assertEqual(settings.changes(single, both),
                         [('Trips', '—', 'added: FRA → AMS, 2026-10-20 to 2026-10-23, 3–4 days')])
        self.assertEqual(settings.changes(both, single),
                         [('Trips', 'FRA → AMS, 2026-10-20 to 2026-10-23, 3–4 days', 'removed')])
        values = trips_values(primary_trip='ams', max_http_attempts_per_run=1800)
        values['trips'][1]['good_deal_nonstop_eur'] = 99
        self.assertEqual(settings.changes(both, Settings.from_dict(values)), [
            ('Request budget per run', '1600', '1800'), ('Shown first on the website', 'BKK', 'AMS'),
            ('AMS · Price target, non-stop (€)', '120', '99')])
        twins = trips_values()
        twins['trips'][1].update(destination='BKK', origins=['MUC'])
        self.assertEqual(settings.trip_name(Settings.from_dict(twins).trip('ams'), Settings.from_dict(twins).trips),
                         'BKK (ams)')


@unittest.skipUnless(shutil.which('git'), 'git not installed')
class BranchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        git('init', '-q', '--bare', 'remote.git', cwd=root)
        self.work = root / 'work'
        self.work.mkdir()
        git('init', '-q', cwd=self.work)
        git('remote', 'add', 'origin', '../remote.git', cwd=self.work)
        self.config = self.work / 'config.json'
        shutil.copyfile(ROOT / 'config.json', self.config)
        self.original = self.config.read_text(encoding='utf-8')
        cwd = os.getcwd()
        os.chdir(self.work)
        self.addCleanup(os.chdir, cwd)
        self.addCleanup(self.temp.cleanup)
        self.values = {**json.loads(self.original), 'destination': 'HND', 'departure_end': '2026-10-25'}

    def test_without_branch_main_config_applies_unchanged(self):
        settings.use(self.config)
        self.assertEqual(self.config.read_text(encoding='utf-8'), self.original)

    def test_apply_stores_settings_on_branch_and_use_loads_them(self):
        outcome, reply = settings.apply(body(self.values), self.config, TODAY, '7')
        self.assertEqual(outcome, 'applied')
        self.assertIn('| Destination | BKK | HND |', reply)
        self.assertIn('price history and alerts start over', reply)
        # main's config.json is not touched by apply.
        self.assertEqual(self.config.read_text(encoding='utf-8'), self.original)
        settings.use(self.config)
        self.assertEqual(Config.load(self.config).destination, 'HND')
        self.assertEqual(settings.apply(body(self.values), self.config, TODAY)[0], 'unchanged')
        later = {**self.values, 'good_deal_layover_eur': 700}
        self.assertEqual(settings.apply(body(later), self.config, TODAY)[0], 'applied')
        log = subprocess.run(['git', '--git-dir=../remote.git', 'log', '--format=%s', 'search-config'],
                             capture_output=True, text=True, check=True)
        self.assertEqual(log.stdout.splitlines(), ['Search settings: DUS, FRA, AMS → HND',
                                                   'Search settings from issue #7: DUS, FRA, AMS → HND'])

    def test_trips_are_stored_and_a_single_trip_form_edits_the_primary_trip(self):
        outcome, reply = settings.apply(body(trips_values()), self.config, TODAY, '8')
        self.assertEqual(outcome, 'applied')
        self.assertIn('✅ **New searches applied:** DUS, FRA, AMS → BKK · FRA → AMS', reply)
        self.assertIn('| Trips | — | added: FRA → AMS, 2026-10-20 to 2026-10-23, 3–4 days |', reply)
        self.assertIn('All 2 trips are searched in every run', reply)
        self.assertNotIn('start over', reply)  # Bangkok keeps its history.
        settings.use(self.config)
        stored = Settings.load(self.config)
        self.assertEqual([t.id for t in stored.trips], ['main', 'ams'])
        self.assertEqual(stored.primary.scope(), Config.load(ROOT / 'config.json').scope())
        # The current single-trip form sends one trip: it replaces the primary trip only.
        outcome, reply = settings.apply(body({**json.loads(self.original), 'good_deal_layover_eur': 600}), self.config, TODAY)
        self.assertEqual(outcome, 'applied')
        self.assertIn('| BKK · Price target, with stops (€) | 650 | 600 |', reply)
        settings.use(self.config)
        stored = Settings.load(self.config)
        self.assertEqual(stored.trip('main').good_deal_layover_eur, 600)
        self.assertEqual(stored.trip('ams').destination, 'AMS')
        # The trip shown first can be changed later.
        outcome, reply = settings.apply(body(trips_values(primary_trip='ams')), self.config, TODAY)
        self.assertIn('| Shown first on the website | BKK | AMS |', reply)
        settings.use(self.config)
        self.assertEqual(Settings.load(self.config).primary.destination, 'AMS')

    def test_the_form_s_single_trip_is_stored_in_the_flat_layout(self):
        values = trips_values()
        values['trips'] = values['trips'][:1]
        values['trips'][0]['good_deal_layover_eur'] = 600
        outcome, reply = settings.apply(body(values), self.config, TODAY)
        self.assertEqual(outcome, 'applied')
        self.assertIn('| Price target, with stops (€) | 650 | 600 |', reply)
        settings.use(self.config)
        stored = json.loads(self.config.read_text(encoding='utf-8'))
        self.assertEqual(stored, {**json.loads(self.original), 'good_deal_layover_eur': 600})

    def test_rejected_settings_store_nothing(self):
        outcome, reply = settings.apply(body({**self.values, 'origins': ['XQZ']}), self.config, TODAY)
        self.assertEqual(outcome, 'rejected')
        self.assertIn('current search keeps running unchanged', reply)
        self.assertEqual(settings.stored(), (None, None))

    def test_invalid_stored_settings_fail_closed(self):
        settings.save('{"origins": ["DUS"], "destination": "DUS"}\n', None, 'broken\n')
        with self.assertRaises(ValueError):
            settings.use(self.config)
        self.assertEqual(self.config.read_text(encoding='utf-8'), self.original)


@unittest.skipUnless(shutil.which('node'), 'node not installed')
class WebsiteFormTests(unittest.TestCase):
    def node(self, script):
        result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=ROOT, capture_output=True,
                                text=True, check=True)
        return json.loads(result.stdout)

    def test_form_issue_is_accepted_and_estimates_match(self):
        from scripts.build_site import form_data
        meta = form_data(Settings.load(ROOT / 'config.json'))
        main = meta['trips'][0]
        trips = [{'id': 'main', 'config': {**main, 'origins': ['DUS', 'MUC'], 'destination': 'HND',
                                            'display_names': {'HND': 'Tokio'}}},
                 {'id': None, 'config': {**main, 'origins': ['FRA'], 'destination': 'AMS', 'min_trip_days': 3,
                                         'max_trip_days': 4, 'max_stops': 0, 'airlines_exclude': ['FR']}}]
        script = ("import {issueBody,settingsJson,settingsChanges,requestEstimate} from './website/search-model.mjs';"
                  f"const meta={json.dumps(meta)}, trips={json.dumps(trips)};"
                  "const settings=settingsJson(meta,trips,1);"
                  "console.log(JSON.stringify({body:issueBody(meta,settings,settingsChanges(meta,trips,1)),"
                  "estimates:trips.map(t=>requestEstimate(t.config,'2026-10-21'))}));")
        result = self.node(script)
        parsed = settings.parse_issue(result['body'])
        self.assertEqual([t.id for t in parsed.trips], ['main', 'ams'])
        self.assertEqual(parsed.primary_trip, 'ams')
        self.assertEqual(parsed.trip('main'), Config.from_dict(trips[0]['config']))
        self.assertEqual(parsed.trip('ams'), Config.from_dict({**trips[1]['config'], 'id': 'ams'}))
        for trip, estimate in zip(parsed.trips, result['estimates']):
            expected = request_estimate(trip, date(2026, 10, 21))
            self.assertEqual({k: estimate[k] for k in expected}, expected)


if __name__ == '__main__':
    unittest.main()

from datetime import date
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from scripts import search_settings as settings
from tracker.config import Config
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
            'Sucheinstellungen': body(self.values, marker=False),
            'Einstellungsblock': settings.MARKER + '\nkein JSON',
            'kein gültiges JSON': settings.MARKER + '\n```json\n{oops\n```',
            'Es fehlen Einstellungen: destination': body({k: v for k, v in self.values.items() if k != 'destination'}),
            'Ungültige Einstellung': body({**self.values, 'max_trip_days': 200}),
            'Ungültige Einstellung: Unknown config keys': body({**self.values, 'surprise': 1}),
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
        with self.assertRaisesRegex(settings.Rejected, 'kein Tag ab morgen'):
            check(departure_start='2026-09-01', departure_end='2026-10-01')
        with self.assertRaisesRegex(settings.Rejected, 'Zu viele Anfragen'):
            check(departure_end='2026-12-31')
        with self.assertRaisesRegex(settings.Rejected, 'Zu lange Suche'):
            check(request_interval_seconds=30)

    def test_change_table_uses_readable_labels(self):
        old, new = Config(), Config(destination='HND', travel_class='business', hide_separate_tickets=False)
        self.assertEqual(settings.changes(old, new), [
            ('Ziel', 'BKK', 'HND'), ('Reiseklasse', 'Economy', 'Business'),
            ('Getrennte Tickets ausblenden', 'ja', 'nein')])
        self.assertEqual(settings.cell('a|b'), 'a\\|b')


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
        self.assertIn('| Ziel | BKK | HND |', reply)
        self.assertIn('Preisverlauf und Preisalarme beginnen', reply)
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

    def test_rejected_settings_store_nothing(self):
        outcome, reply = settings.apply(body({**self.values, 'origins': ['XQZ']}), self.config, TODAY)
        self.assertEqual(outcome, 'rejected')
        self.assertIn('bisherige Suche läuft unverändert weiter', reply)
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
        meta = {'config': {**Config.load(ROOT / 'config.json').file_dict(), 'display_names': {}},
                'labels': settings.LABELS, 'travel_classes': settings.CLASSES, 'marker': settings.MARKER,
                'repository': 'Fabiano225/flight-tracker'}
        values = {**meta['config'], 'origins': ['DUS', 'MUC'], 'destination': 'HND', 'display_names': {'HND': 'Tokio'}}
        script = ("import {issueBody,requestEstimate} from './website/search-model.mjs';"
                  f"const meta={json.dumps(meta)}, values={json.dumps(values)};"
                  "console.log(JSON.stringify({body:issueBody(meta,values),"
                  "estimate:requestEstimate(values,'2026-10-21')}));")
        result = self.node(script)
        config = settings.parse_issue(result['body'])
        self.assertEqual(config, Config.from_dict(values))
        expected = request_estimate(config, date(2026, 10, 21))
        self.assertEqual({k: result['estimate'][k] for k in expected}, expected)


if __name__ == '__main__':
    unittest.main()

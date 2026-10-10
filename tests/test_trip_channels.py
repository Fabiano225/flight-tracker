"""Each trip's messages in its own Discord channel or Telegram chat, if one is set."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

from tracker.config import Config, Settings
from tracker.discord import Discord
from tracker.network import ServiceError
from tracker.notifications import Router, configured_router, deliver, trip_channels
from tracker.provider import Quote
from tracker.store import Store, stamp
from test_tracker import NOW

ROOT = Path(__file__).resolve().parents[1]
WEBHOOK = 'https://discord.com/api/webhooks/123456/fake-test-token'
FRIEND = 'https://discord.com/api/webhooks/654321/fake-friend-token'
MAIN = Config()
BER = Config(id='ber', origins=('BKK',), destination='BER')
SETTINGS = Settings((MAIN, BER))


def sender(*ids, destination=None):
    return NS(destination=destination or 'discord:' + ids[0], send=Mock(side_effect=list(ids)))


class TripChannelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        for run, trip in (('main-run', MAIN), ('ber-run', BER)):
            self.store.db.execute('INSERT INTO runs VALUES(?,?,?,?,?)',
                                  (run, stamp(NOW), trip.scope(), 'ok', json.dumps({'config': trip.public_dict()})))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def queue(self, run, kind, text):
        return self.store.enqueue(run, kind, NOW, text)

    def status(self, message):
        return self.store.db.execute('SELECT status FROM outbox WHERE id=?', (message,)).fetchone()[0]

    def test_each_trip_s_messages_reach_its_own_channel_and_health_the_main_one(self):
        self.queue('main-run', 'notice', 'BKK search window ended')
        self.queue('ber-run', 'notice', 'BER search window ended')
        self.queue('ber-run', 'health', 'Flight tracker needs attention.')
        main, friend = sender('1', '2'), sender('9')
        self.assertEqual(deliver(self.store, SETTINGS, NOW, Router(lambda: main, {'ber': lambda: friend})), 3)
        self.assertEqual([call.args[0] for call in main.send.call_args_list],
                         ['BKK search window ended', 'Flight tracker needs attention.'])
        friend.send.assert_called_once_with('BER search window ended')

    def test_a_failing_channel_holds_back_only_its_own_messages(self):
        mine, theirs = self.queue('main-run', 'notice', 'BKK'), self.queue('ber-run', 'notice', 'BER')
        main, friend = sender('1'), NS(destination='discord:friend', send=Mock(side_effect=ServiceError('API HTTP 404')))
        with self.assertRaisesRegex(ServiceError, 'Delivered 1 messages; failed: trip ber: API HTTP 404'):
            deliver(self.store, SETTINGS, NOW, Router(lambda: main, {'ber': lambda: friend}))
        self.assertEqual((self.status(mine), self.status(theirs)), ('sent', 'pending'))
        friend.send = Mock(return_value='9')
        self.assertEqual(deliver(self.store, SETTINGS, NOW, Router(lambda: main, {'ber': lambda: friend})), 1)

    def test_without_a_main_channel_only_trips_with_their_own_get_messages(self):
        mine, theirs = self.queue('main-run', 'notice', 'BKK'), self.queue('ber-run', 'notice', 'BER')
        friend = sender('9')
        self.assertEqual(deliver(self.store, SETTINGS, NOW, Router(None, {'ber': lambda: friend})), 1)
        self.assertEqual((self.status(mine), self.status(theirs)), ('pending', 'sent'))

    def test_a_check_status_replies_in_the_trip_s_own_channel(self):
        q = Quote('BKK', '2026-10-20', '2026-11-03', 'nonstop', 60000, 700, 750, 0, 0, 'TG', '')
        alert = self.store.enqueue('ber-run', 'trend', NOW, 'BER price alert', [q], BER.scope())
        status = self.store.enqueue('ber-run', 'check_status', NOW, 'BER check status', [q], BER.scope())
        self.store.db.execute('INSERT INTO outbox_replies VALUES(?,?)', (status, alert))
        main, friend = sender('1'), sender('41', '42')
        deliver(self.store, SETTINGS, NOW, Router(lambda: main, {'ber': lambda: friend}))
        friend.send.assert_called_with('BER check status', reply_to_message_id='41')
        main.send.assert_not_called()

    def test_channels_by_trip_id_from_secrets(self):
        env = {'DISCORD_WEBHOOK_URL': WEBHOOK, 'DISCORD_WEBHOOKS': json.dumps({'ber': FRIEND}),
               'TELEGRAM_BOT_TOKEN': 'fake', 'TELEGRAM_CHAT_IDS': json.dumps({'main': 1234, 'ber': '777'})}
        with patch.dict(os.environ, env, clear=True):
            router = configured_router(SETTINGS)
            # Discord wins for a trip listed in both; the other gets its Telegram chat.
            self.assertEqual(router.channel('ber').url, FRIEND)
            bot = router.channel('main')
            self.assertEqual(bot.chat_id, '1234')
            self.assertTrue(bot.destination.startswith('telegram:'))
            self.assertIsInstance(router.channel(None), Discord)
            self.assertIsNone(router.key('other'))
        with patch.dict(os.environ, {'TELEGRAM_CHAT_IDS': '{"ber": "777"}'}, clear=True):
            router = configured_router(SETTINGS)
            self.assertIsNone(router.channel(None))  # No main channel set up.
            with self.assertRaisesRegex(ServiceError, 'TELEGRAM_BOT_TOKEN'):
                router.channel('ber')

    def test_secret_errors_never_repeat_a_value(self):
        for text in ('not json', '["x"]', '{"Bad Trip": "x"}', '{"ber": ""}', '{"ber": ["x"]}'):
            with self.subTest(text=text), patch.dict(os.environ, {'DISCORD_WEBHOOKS': text}, clear=True):
                with self.assertRaises(ServiceError) as caught:
                    trip_channels('DISCORD_WEBHOOKS')
                self.assertNotIn('not json', str(caught.exception))
        with patch.dict(os.environ, {'DISCORD_WEBHOOKS': '{"ber": "https://evil.example/secret-token"}'}, clear=True):
            with self.assertRaisesRegex(ServiceError, 'entry for trip ber') as caught:
                configured_router(SETTINGS).channel('ber')
            self.assertNotIn('secret-token', str(caught.exception))
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(trip_channels('DISCORD_WEBHOOKS'), {})

    def test_a_channel_for_a_trip_that_is_not_searched_is_reported(self):
        env = {**os.environ, 'DISCORD_WEBHOOKS': json.dumps({'nowhere': FRIEND}), 'NOTIFICATION_CHANNEL': '',
               'DISCORD_WEBHOOK_URL': '', 'TELEGRAM_BOT_TOKEN': '', 'TELEGRAM_CHAT_ID': '', 'TELEGRAM_CHAT_IDS': ''}
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run([sys.executable, '-m', 'tracker', 'notify', '--state-dir', temp],
                                    cwd=ROOT, capture_output=True, text=True, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('::warning::DISCORD_WEBHOOKS names the trip nowhere, which is not searched', result.stdout)
        self.assertIn('Delivered 0 messages', result.stdout)


if __name__ == '__main__':
    unittest.main()

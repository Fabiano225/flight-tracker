from datetime import timedelta
from io import BytesIO
import json
import os
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from tracker.config import Config
from tracker.discord import Discord, payload_for, GREEN, RED, AMBER
from tracker.network import JsonHttp, ServiceError
from tracker.check_status import TELEGRAM_REPLY_HINT
from tracker.notifications import ARCHIVE_PREFIX, SETTINGS, configured_sender, deliver
from tracker.provider import Quote, DemoProvider
from tracker.service import scan
from tracker.store import Store, stamp
from tracker.telegram import Telegram
from tracker.trends import block
from test_tracker import NOW

WEBHOOK = 'https://discord.com/api/webhooks/123456/fake-test-token'


class DiscordTests(unittest.TestCase):
    def test_auto_prefers_discord_and_explicit_telegram_remains_available(self):
        with patch.dict(os.environ, {'DISCORD_WEBHOOK_URL': WEBHOOK, 'NOTIFICATION_CHANNEL': '',
                'TELEGRAM_BOT_TOKEN': 'fake', 'TELEGRAM_CHAT_ID': '123'}, clear=True):
            self.assertIsInstance(configured_sender(), Discord)
            os.environ['NOTIFICATION_CHANNEL'] = 'telegram'
            self.assertIsInstance(configured_sender(), Telegram)
            os.environ['NOTIFICATION_CHANNEL'] = 'invalid'
            with self.assertRaises(ServiceError):
                configured_sender()

    def test_auto_without_discord_retains_telegram(self):
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'fake', 'TELEGRAM_CHAT_ID': '123'}, clear=True):
            self.assertIsInstance(configured_sender(), Telegram)
            os.environ['NOTIFICATION_CHANNEL'] = 'discord'
            with self.assertRaises(ServiceError):
                configured_sender()

    def test_webhook_validation_never_discloses_bad_url(self):
        for url in ('https://evil.example/private', WEBHOOK + '?thread_id=1', WEBHOOK + '#private',
                    WEBHOOK.replace('https:', 'http:'), WEBHOOK.replace('discord.com', 'discord.com.evil.example'),
                    WEBHOOK.replace('discord.com', 'private@discord.com')):
            with self.subTest(url=url), self.assertRaises(ServiceError) as ctx:
                Discord(url)
            self.assertNotIn('private', str(ctx.exception))
            self.assertNotIn('fake-test-token', str(ctx.exception))

    def test_wait_acknowledgement_and_no_mentions(self):
        http = NS(get_json=Mock(return_value={'id': '555'}))
        sender = Discord(WEBHOOK, http)
        self.assertEqual(sender.send('Test @everyone'), '555')
        url, headers, body = http.get_json.call_args.args
        self.assertTrue(url.endswith('?wait=true'))
        payload = json.loads(body)
        self.assertEqual(payload['allowed_mentions'], {'parse': []})
        self.assertIn('embeds', payload)
        self.assertNotIn('fake-test-token', sender.destination)
        for response in ({}, {'id': None}, {'id': 'bad'}):
            http.get_json.return_value = response
            with self.assertRaises(ServiceError):
                sender.send('test')

    def test_status_link_uses_runtime_metadata_and_no_unsupported_reply(self):
        http = NS(get_json=Mock(side_effect=[{'guild_id': '111', 'channel_id': '222'}, {'id': '556'}]))
        sender = Discord(WEBHOOK, http)
        sender.send('BKK check status\nNo price change for the watched offers.\n' + TELEGRAM_REPLY_HINT, '555')
        payload = json.loads(http.get_json.call_args.args[2])
        self.assertIn('https://discord.com/channels/111/222/555', str(payload))
        self.assertNotIn('quoted message', str(payload))
        self.assertNotIn('message_reference', payload)
        self.assertEqual(payload['embeds'][0]['color'], GREEN)

    def test_optional_reference_failure_does_not_lose_status(self):
        http = NS(get_json=Mock(side_effect=[ServiceError('unavailable'), {'id': '556'}]))
        sender = Discord(WEBHOOK, http)
        self.assertEqual(sender.send('BKK check status\nLatest related price alert: yesterday', '555'), '556')
        self.assertIn('currently unavailable', str(json.loads(http.get_json.call_args.args[2])))

    def test_price_cards_preserve_price_dates_verdict_and_link(self):
        q = Quote('FRA', '2026-10-20', '2026-11-03', 'nonstop', 60000, 700, 750, 0, 0, 'TG', '')
        history = dict(previous=66000, previous_at=stamp(NOW), low=66000, count=4, first_at=stamp(NOW))
        prior = dict(departure=q.departure, return_date=q.return_date, price=66000, created=stamp(NOW))
        text = 'BKK price alert\n' + stamp(NOW) + '\n' + block(q, history, prior, Config()) + '\n\nNot a forecast.'
        payload = payload_for(text)
        card = payload['embeds'][1]
        self.assertEqual(payload['embeds'][0]['title'], '✈️ Bangkok · Price update')
        self.assertEqual(card['color'], GREEN)
        self.assertIn('2026-10-20 to 2026-11-03 | 600.00 EUR', card['description'])
        self.assertIn('660.00 EUR → 600.00 EUR', card['description'])
        self.assertIn('**CHECK TO BUY:', card['description'])
        self.assertTrue(card['url'].startswith('https://www.google.com/travel/flights'))
        rise = text.replace('PRICE DROPPED', 'PRICE ROSE')
        self.assertEqual(payload_for(rise)['embeds'][1]['color'], RED)
        self.assertIn('Not a forecast', str(payload))

    def test_legacy_german_messages_still_render(self):
        # Messages queued before the switch to English may still be pending.
        text = ('BKK Preisalarm\n' + stamp(NOW) + '\nPREIS GESUNKEN | FRA-BKK | DIREKT (beide Richtungen)\n'
                '2026-10-20 bis 2026-11-03 | 600.00 EUR\nKAUF PRUEFEN: innerhalb deiner Zielgrenze.\n'
                'https://www.google.com/travel/flights?q=x')
        payload = payload_for(text)
        self.assertEqual(payload['embeds'][1]['color'], GREEN)
        self.assertIn('**KAUF PRÜFEN:', payload['embeds'][1]['description'])
        self.assertEqual(payload_for('BKK Suchstatus\nKeine Preisänderung')['embeds'][0]['color'], GREEN)
        self.assertEqual(payload_for('BKK Suchstatus\nPrüfung unvollständig')['embeds'][0]['color'], AMBER)
        self.assertIn('historical', payload_for('Übernommener Preisstand – alt')['embeds'][0]['title'])

    def test_titles_name_the_destination_of_each_message(self):
        older = payload_for('BKK price alert\n' + stamp(NOW) + '\n\nNot a forecast.', code='HND')
        self.assertEqual((older['username'], older['embeds'][0]['title']), ('BKK Flight Tracker', '✈️ Bangkok · Price update'))
        tokyo = payload_for('HND price alert\n' + stamp(NOW) + '\n\nx', code='HND', names={'HND': 'Tokyo Haneda'})
        self.assertEqual((tokyo['username'], tokyo['embeds'][0]['title']), ('HND Flight Tracker', '✈️ Tokyo Haneda · Price update'))
        other = payload_for('Note', code='HND')
        self.assertEqual(other['embeds'][0]['title'], '✈️ Tokyo · Flight Tracker')
        self.assertEqual(payload_for('Note')['embeds'][0]['title'], '✈️ Flight Tracker')
        ended = payload_for('BKK search window ended\nDepartures have passed.\nSet up a new search: ' + SETTINGS)
        self.assertEqual(ended['embeds'][0]['title'], '🏁 Bangkok · Search window ended')
        self.assertIn('[Set up a new search →](' + SETTINGS + ')', ended['embeds'][0]['description'])
        with patch.dict(os.environ, {'DISCORD_WEBHOOK_URL': WEBHOOK}, clear=True):
            sender = configured_sender(Config(destination='HND', display_names={'HND': 'Tokyo Haneda'}))
        self.assertEqual((sender.code, sender.names), ('HND', {'HND': 'Tokyo Haneda'}))

    def test_partial_is_amber_and_historical_not_a_new_deal(self):
        self.assertEqual(payload_for('BKK check status\nCheck incomplete – x')['embeds'][0]['color'], AMBER)
        card = payload_for(ARCHIVE_PREFIX + ' – NOT a new price alert.\nHistorical message')['embeds'][0]
        self.assertIn('historical', card['title'])

    def test_maximum_legacy_text_is_preserved_within_embed_limits(self):
        text = 'x' * 4096
        payload = payload_for(text)
        self.assertEqual(''.join(e['description'] for e in payload['embeds']), text)
        self.assertTrue(all(len(e['description']) <= 4096 for e in payload['embeds']))
        with self.assertRaises(ValueError):
            payload_for('x' * 6001)

    def test_real_pipeline_messages_render_without_network(self):
        with tempfile.TemporaryDirectory() as directory, Store(directory, 'demo') as store:
            config = Config()
            scan(config, store, DemoProvider(config), NOW, demo=True)
            messages = store.db.execute('SELECT text FROM outbox').fetchall()
            self.assertTrue(messages)
            for row in messages:
                text = row[0].replace('DEMO - synthetic prices', 'BKK price alert')
                payload = payload_for(text)
                self.assertLessEqual(len(payload['embeds']), 10)
                self.assertIn('Price', str(payload))

    def test_rate_limit_json_fractional_seconds_and_redaction(self):
        sleeps = []
        exc = HTTPError(WEBHOOK, 429, 'secret', {}, BytesIO(b'{"retry_after": 3.5}'))
        http = JsonHttp(opener=NS(open=Mock(side_effect=[exc, BytesIO(b'{"id":"555"}')])), sleep=sleeps.append)
        self.assertEqual(Discord(WEBHOOK, http).send('test'), '555')
        self.assertIn(3.5, sleeps)
        exc = HTTPError(WEBHOOK, 404, 'fake-test-token', {}, BytesIO(b'fake-test-token'))
        http = JsonHttp(opener=NS(open=Mock(side_effect=exc)))
        with self.assertRaises(ServiceError) as ctx:
            Discord(WEBHOOK, http).send('test')
        self.assertNotIn('fake-test-token', str(ctx.exception))


class ChannelMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(self.tmp.name)
        self.store.db.execute("INSERT INTO runs VALUES('r',?,?,'ok',?)", (stamp(NOW), Config().scope(), json.dumps({'config': Config().public_dict()})))
        q = Quote('FRA', '2026-10-20', '2026-11-03', 'nonstop', 60000, 700, 750, 0, 0, 'TG', '')
        self.price = self.store.enqueue('r', 'trend', NOW-timedelta(hours=6), 'Historical 600 EUR', [q], Config().scope())
        self.store.db.execute("UPDATE outbox SET status='sent',message_id='42' WHERE id=?", (self.price,))
        self.status = self.store.enqueue('r', 'check_status', NOW, 'No change', [q], Config().scope())
        self.store.db.execute('INSERT INTO outbox_replies VALUES(?,?)', (self.status, self.price))
        self.sender = NS(destination=Discord(WEBHOOK).destination, send=Mock(side_effect=['555', '556']))

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_switch_copies_reference_once_and_preserves_history(self):
        self.assertEqual(deliver(self.store, Config(), NOW, self.sender), 2)
        self.assertIn('NOT a new price alert', self.sender.send.call_args_list[0].args[0])
        self.sender.send.assert_called_with('No change', reply_to_message_id='555')
        self.assertEqual(deliver(self.store, Config(), NOW, self.sender), 0)
        self.assertEqual(self.store.db.execute('SELECT message_id FROM outbox WHERE id=?', (self.price,)).fetchone()[0], '42')
        dump = '\n'.join(self.store.db.iterdump())
        self.assertNotIn('fake-test-token', dump)
        self.assertNotIn('webhooks/', dump)

    def test_partial_delivery_survives_reopen_without_recopying_reference(self):
        self.sender.send.side_effect = ['555', ServiceError('test failure')]
        with self.assertRaises(ServiceError):
            deliver(self.store, Config(), NOW, self.sender)
        self.store.close()
        self.store = Store(self.tmp.name)
        self.sender.send = Mock(return_value='556')
        self.assertEqual(deliver(self.store, Config(), NOW, self.sender), 1)
        self.sender.send.assert_called_once_with('No change', reply_to_message_id='555')

    def test_failed_reference_keeps_pending_status(self):
        self.sender.send.side_effect = ServiceError('test failure')
        with self.assertRaises(ServiceError):
            deliver(self.store, Config(), NOW, self.sender)
        self.assertEqual(self.store.db.execute('SELECT status FROM outbox WHERE id=?', (self.status,)).fetchone()[0], 'pending')
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM delivery_receipts').fetchone()[0], 0)

    def test_new_discord_destination_never_reuses_previous_message_id(self):
        self.store.db.execute('INSERT INTO delivery_receipts VALUES(?,?,?,?)',
            (self.price, 'discord:different-webhook', '777', stamp(NOW)))
        deliver(self.store, Config(), NOW, self.sender)
        self.sender.send.assert_called_with('No change', reply_to_message_id='555')

    def test_telegram_return_does_not_reply_to_discord_id(self):
        self.store.db.execute('INSERT INTO delivery_receipts VALUES(?,?,?,?)',
            (self.price, self.sender.destination, '777', stamp(NOW)))
        sender = NS(send=Mock(side_effect=['43', '44']))
        deliver(self.store, Config(), NOW, sender)
        sender.send.assert_called_with('No change', reply_to_message_id='43')


if __name__ == '__main__':
    unittest.main()

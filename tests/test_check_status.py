from dataclasses import replace
from datetime import timedelta
import json
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from tracker.check_status import queue_check_status
from tracker.config import Config
from tracker.provider import Quote
from tracker.store import Store, stamp
from tracker.telegram import Telegram, deliver
from tracker.trends import watch_key
from test_tracker import NOW


class CheckStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name)
        self.config = Config()
        self.scope = self.config.scope()
        self.q = Quote('FRA','2026-10-15','2026-10-29','layover',60000,900,950,1,1,'TG','')
        self.summary = dict(status='ok',queued_deals=0,calendar_queries_ok=48,calendar_queries_planned=48,
                            config=self.config.public_dict())
        for run, dt in [('old', NOW-timedelta(hours=6)),('new', NOW)]:
            self.store.db.execute('INSERT INTO runs VALUES(?,?,?,?,?)',
                                  (run,stamp(dt),self.scope,'ok',json.dumps(self.summary)))
        self.old = self.store.enqueue('old','trend',NOW-timedelta(hours=6),'Old price',[self.q],self.scope)
        self.store.db.execute("UPDATE outbox SET status='sent',message_id='42' WHERE id=?",(self.old,))
        self.store.set_meta(watch_key(self.config,self.scope),json.dumps({'FRA:layover':self.q.to_dict()}))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def queue(self, price=60000, summary=None, missing=False):
        q = replace(self.q,price=price)
        verified = {} if missing else {(q.origin,q.departure,q.return_date,q.category):q}
        return queue_check_status(self.store,self.config,self.scope,'new',verified,NOW,summary or self.summary)

    def text(self):
        return self.store.db.execute("SELECT text FROM outbox WHERE kind='check_status'").fetchone()[0]

    def test_exactly_unchanged_replies_to_price_and_not_to_health(self):
        self.store.enqueue('old','health',NOW,'Health')
        self.queue()
        self.assertIn('Keine Preisänderung', self.text())
        self.assertFalse(self.queue())
        sender = NS(send=Mock(return_value='43'))
        deliver(self.store,self.config,NOW,sender)
        sender.send.assert_any_call(self.text(),reply_to_message_id='42')
        self.assertEqual(deliver(self.store,self.config,NOW,sender),0)

    def test_small_increase_and_decrease_are_not_labelled_unchanged(self):
        self.queue(price=61000)
        self.assertIn('Nur kleine Preisänderungen',self.text())
        self.assertIn('+10.00 EUR',self.text())
        self.assertNotIn('Keine Preisänderung',self.text())

    def test_partial_scan_does_not_claim_no_change(self):
        self.queue(summary={**self.summary,'status':'partial','calendar_queries_ok':46})
        self.assertIn('Prüfung unvollständig',self.text())
        self.assertNotIn('Keine Preisänderung',self.text())

    def test_small_decrease(self):
        self.queue(price=59700)
        self.assertIn('Nur kleine Preisänderungen',self.text())
        self.assertIn('-3.00 EUR',self.text())

    def test_pending_original_is_not_treated_as_delivered(self):
        self.store.db.execute("UPDATE outbox SET status='pending' WHERE id=?",(self.old,))
        self.queue()
        self.assertIn('Prüfung unvollständig',self.text())
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM outbox_replies').fetchone()[0],0)

    def test_missing_watch_is_not_unchanged(self):
        self.queue(missing=True)
        self.assertIn('Prüfung unvollständig',self.text())

    def test_price_alert_or_expired_window_has_no_extra_status(self):
        self.assertFalse(self.queue(summary={**self.summary,'queued_deals':1}))
        self.assertFalse(self.queue(summary={**self.summary,'status':'expired'}))

    def test_no_baseline_still_delivers_incomplete_status_without_reply(self):
        self.store.set_meta(watch_key(self.config,self.scope),'{}')
        self.queue(missing=True,summary={**self.summary,'status':'partial'})
        sender = NS(send=Mock(return_value='43'))
        self.assertEqual(deliver(self.store,self.config,NOW,sender),1)
        sender.send.assert_called_once_with(self.text())

    def test_new_window_expires_old_status(self):
        self.queue()
        sender = NS(send=Mock())
        self.assertEqual(deliver(self.store,replace(self.config,departure_start='2026-10-16'),NOW,sender),0)
        sender.send.assert_not_called()

    def test_reply_survives_reopen_and_does_not_store_chat_id(self):
        self.queue()
        self.store.close()
        self.store = Store(self.temp.name)
        target = self.store.db.execute('SELECT target_id FROM outbox_replies').fetchone()[0]
        self.assertEqual(target,self.old)
        sender = NS(send=Mock(return_value='43'))
        self.assertEqual(deliver(self.store,self.config,NOW,sender),1)
        sender.send.assert_called_once_with(self.text(),reply_to_message_id='42')

    def test_failed_send_keeps_status_pending(self):
        self.queue()
        sender=NS(send=Mock(side_effect=RuntimeError('test')))
        with self.assertRaises(RuntimeError):
            deliver(self.store,self.config,NOW,sender)
        self.assertEqual(self.store.db.execute("SELECT status FROM outbox WHERE kind='check_status'").fetchone()[0],'pending')

    def test_telegram_reply_payload_allows_deleted_original(self):
        http = NS(get_json=Mock(return_value={'ok':True,'result':{'message_id':43}}))
        bot=Telegram('test-token','test-chat',http)
        bot.send('Status',reply_to_message_id='42')
        body=json.loads(http.get_json.call_args.args[2])
        self.assertEqual(body['reply_parameters'],{'message_id':42,'allow_sending_without_reply':True})
        bot.send('Plain')
        self.assertNotIn('reply_parameters',json.loads(http.get_json.call_args.args[2]))

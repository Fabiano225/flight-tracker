from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

from tracker.alerts import is_drop, reason_for, digest
from tracker.config import Config, cents
from tracker.network import JsonHttp, ServiceError, BudgetError
from tracker.planner import plan
from tracker.provider import DemoProvider, Quote, normalize_pairs, GuardedClient
from tracker.service import scan
from tracker.store import Store, stamp
from tracker.telegram import Telegram, deliver

NOW = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)


class PlanningTests(unittest.TestCase):
    def test_full_range_and_return_boundary(self):
        batches = plan(Config(), NOW.date())
        self.assertEqual(len(batches), 48)
        triples = {(b.origin, *p) for b in batches for p in b.pairs()}
        self.assertEqual(len(triples), 240)
        self.assertIn(("AMS", "2026-10-23", "2026-11-13"), triples)
        self.assertNotIn(("DUS", "2026-10-13", "2026-10-27"), triples)
        for origin in Config().origins:
            for days in range(14, 22):
                ret = (date(2026, 10, 14) + timedelta(days=days)).isoformat()
                self.assertIn((origin, "2026-10-14", ret), triples)
        self.assertIn(("DUS", "2026-10-15", "2026-10-29"), triples)
        self.assertEqual(sum(len(b.pairs()) for b in batches), 480)
        for _, dep, ret in triples:
            self.assertTrue("2026-10-14" <= dep <= "2026-10-23")
            self.assertIn((date.fromisoformat(ret)-date.fromisoformat(dep)).days, range(14,22))

    def test_past_and_same_day_skipped(self):
        self.assertTrue(all(dep > "2026-10-20" for b in plan(Config(), date(2026,10,20)) for dep,_ in b.pairs()))
        self.assertEqual(plan(Config(), date(2026,10,23)), [])

    def test_config_isolation(self):
        c = Config()
        self.assertNotEqual(c.scope(), replace(c, checked_bags=1).scope())
        self.assertNotEqual(c.scope(), c.scope("demo"))
        self.assertNotEqual(c.scope(), replace(c, max_direction_minutes=1200).scope())
        self.assertEqual(c.scope(), replace(c, good_deal_layover_eur=600).scope())

    def test_invalid_config(self):
        for kwargs in ({"min_trip_days":22}, {"currency":"USD"}, {"adults":2},
                       {"origins":("DUS","DUS")}, {"max_direction_minutes":1260},
                       {"good_deal_nonstop_eur":0}, {"departure_end":"2026-01-01"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Config(**kwargs)

    def test_price_validation(self):
        self.assertEqual(cents("650.25"), 65025)
        for value in (0,-1,None,True,"NaN","Infinity","cheap"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                cents(value)


def leg(src, dst, day):
    return NS(departure_airport=NS(name=src), arrival_airport=NS(name=dst),
              departure_datetime=datetime.fromisoformat(day), airline=NS(name="TG"))


def pair(out_minutes=700, in_minutes=750, out_stops=0, in_stops=0, price=600, currency="EUR", transfer=False):
    return (NS(legs=[leg("FRA","BKK","2026-10-15")], duration=out_minutes, stops=out_stops,
               currency=currency, price=500, self_transfer=transfer),
            NS(legs=[leg("BKK","FRA","2026-10-29")], duration=in_minutes, stops=in_stops,
               currency=currency, price=price, self_transfer=transfer))


class QuoteTests(unittest.TestCase):
    def normalize(self, pairs, profile="any"):
        return normalize_pairs(pairs,"FRA","2026-10-15","2026-10-29",profile,Config(),lambda _: "https://www.google.com/travel/flights")

    def test_round_trip_price_is_not_double_counted(self):
        self.assertEqual(self.normalize([pair()])[0].price, 60000)

    def test_21_hours_excluded_in_either_direction(self):
        self.assertEqual(len(self.normalize([pair(1259,1259)])),1)
        self.assertEqual(self.normalize([pair(1260,750),pair(700,1260)]),[])

    def test_actual_category_and_direct_profile(self):
        self.assertEqual(self.normalize([pair(in_stops=1)])[0].category,"layover")
        self.assertEqual(self.normalize([pair(in_stops=1)],"nonstop"),[])
        self.assertEqual(self.normalize([pair()])[0].category,"nonstop")

    def test_currency_unknown_price_self_transfer_and_incomplete(self):
        self.assertEqual(self.normalize([pair(currency="USD"),pair(currency=None),pair(price=None),pair(transfer=True)]),[])
        self.assertEqual(self.normalize([pair()[0], (pair()[0],)]),[])

    def test_wrong_dates_or_airports(self):
        bad = pair()
        bad[1].legs[0].departure_airport.name = "DMK"
        self.assertEqual(self.normalize([bad]),[])
        bad = pair()
        bad[1].legs[0].departure_datetime = datetime(2026,10,30)
        self.assertEqual(self.normalize([bad]),[])

    def test_minimum_for_each_category(self):
        quotes = self.normalize([pair(price=700),pair(price=600),pair(in_stops=1,price=550)])
        self.assertEqual({q.category:q.price for q in quotes},{"nonstop":60000,"layover":55000})


class AlertTests(unittest.TestCase):
    def test_drop_requires_both_limits(self):
        c = Config()
        self.assertTrue(is_drop(90000,100000,c))
        self.assertFalse(is_drop(45000,49900,c))
        self.assertFalse(is_drop(145000,150000,c))
        self.assertFalse(is_drop(60000,None,c))

    def test_thresholds_are_separate(self):
        c = replace(Config(),good_deal_nonstop_eur=800,good_deal_layover_eur=650)
        self.assertEqual(reason_for(75000,None,"nonstop",c),"good deal")
        self.assertEqual(reason_for(75000,None,"layover",c),"")

    def test_digest_maximum_fits_telegram(self):
        q = Quote("FRA","2026-10-15","2026-10-29","layover",60000,1200,1259,1,2,"QR, LH","https://www.google.com")
        text = digest([(q,"good deal; drop from EUR 850.00 (29%)")]*6,Config(),NOW)
        self.assertLessEqual(len(text),4096)
        self.assertIn("LAYOVER",text)
        self.assertIn("20h59",text)


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name,"demo")
        self.config = replace(Config(), origins=("FRA",), departure_start="2026-10-15", departure_end="2026-10-15", min_trip_days=14,max_trip_days=14)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def run_scan(self, now=NOW, discount=0):
        return scan(self.config,self.store,DemoProvider(self.config,discount),now,True)

    def test_history_survives_reopen(self):
        self.run_scan()
        self.store.close()
        self.store=Store(self.temp.name,"demo")
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM calendar").fetchone()[0],2)

    def test_baseline_separate_by_category_and_prior_run(self):
        first=self.run_scan()
        scope=self.config.scope("demo")
        args=(scope,"FRA","2026-10-15","2026-10-29")
        self.assertIsNone(self.store.previous_low("quotes",*args,"nonstop",NOW,30,first["run_id"]))
        direct=self.store.previous_low("quotes",*args,"nonstop",NOW+timedelta(hours=6),30,"next")
        connection=self.store.previous_low("quotes",*args,"layover",NOW+timedelta(hours=6),30,"next")
        self.assertEqual(direct-connection,12000)
        self.assertIsNone(self.store.previous_low("quotes",*args,"nonstop",NOW+timedelta(days=31),30,"next"))

    def test_unchanged_deals_not_queued_twice_and_drop_realerts(self):
        first=self.run_scan(discount=200)
        second=self.run_scan(NOW+timedelta(hours=6),200)
        third=self.run_scan(NOW+timedelta(hours=7),300)
        self.assertGreater(first["queued_deals"],0)
        self.assertEqual(second["queued_deals"],0)
        self.assertGreater(third["queued_deals"],0)

    def test_demo_and_live_never_mix(self):
        with self.assertRaises(ValueError):
            Store(self.temp.name,"live")

    def test_provider_failure_is_not_a_zero_fare(self):
        provider=DemoProvider(self.config)
        provider.fetch=Mock(side_effect=ServiceError("Source error"))
        summary=scan(self.config,self.store,provider,NOW,True)
        self.assertEqual(summary["status"],"partial")
        self.assertEqual(summary["queued_deals"],0)
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0],0)
        self.assertIn('health', [r[0] for r in self.store.db.execute('SELECT kind FROM outbox')])


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(self.temp.name)
        self.store.db.execute("INSERT INTO runs VALUES('r',?,?,'ok','{}')",(stamp(NOW),Config().scope()))

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_sent_only_after_ack_and_failures_remain_pending(self):
        self.store.enqueue("r","health",NOW,"test")
        sender=NS(send=Mock(side_effect=ServiceError("Delivery failed")))
        with self.assertRaises(ServiceError):
            deliver(self.store,Config(),NOW,sender)
        self.assertEqual(self.store.db.execute("SELECT status FROM outbox").fetchone()[0],"pending")
        sender.send=Mock(return_value="123")
        self.assertEqual(deliver(self.store,Config(),NOW,sender),1)
        self.assertEqual(deliver(self.store,Config(),NOW,sender),0)

    def test_stale_messages_expire(self):
        self.store.enqueue("r","health",NOW-timedelta(hours=13),"old")
        sender=NS(send=Mock())
        self.assertEqual(deliver(self.store,Config(),NOW,sender),0)
        sender.send.assert_not_called()

    def test_old_window_pending_digest_is_not_delivered(self):
        valid=Quote("FRA","2026-10-15","2026-10-29","nonstop",60000,700,750,0,0,"TG","")
        outside=replace(valid,departure="2026-10-24",return_date="2026-11-07")
        self.store.enqueue("r","deal",NOW,"mixed old digest",[valid,outside],Config().scope())
        self.store.enqueue("r","deal",NOW,"valid new digest",[valid],Config().scope())
        sender=NS(send=Mock(return_value="123"))
        self.assertEqual(deliver(self.store,Config(),NOW,sender),1)
        sender.send.assert_called_once_with("valid new digest")
        self.assertEqual(self.store.db.execute("SELECT status FROM outbox WHERE text='mixed old digest'").fetchone()[0],"expired")

    def test_telegram_body_and_success(self):
        http=NS(get_json=Mock(return_value={"ok":True,"result":{"message_id":42}}))
        bot=Telegram("secret-token","123",http)
        self.assertEqual(bot.send("hello"),"42")
        self.assertNotIn("parse_mode",json.loads(http.get_json.call_args.args[2]))
        http.get_json.return_value={"ok":False}
        with self.assertRaises(ServiceError):
            bot.send("hello")


class NetworkTests(unittest.TestCase):
    def test_retry_then_success_and_count(self):
        response=BytesIO(b'{"ok":true}')
        opener=NS(open=Mock(side_effect=[URLError("private-token"),response]))
        http=JsonHttp(opener=opener,sleep=lambda _:None)
        self.assertEqual(http.get_json("https://example.com"),{"ok":True})
        self.assertEqual(http.used,2)

    def test_error_message_redacts_url_and_body(self):
        exc=HTTPError("https://example.com/secret",403,"secret",{},BytesIO(b'secret'))
        http=JsonHttp(opener=NS(open=Mock(side_effect=exc)),sleep=lambda _:None)
        with self.assertRaises(ServiceError) as ctx:
            http.get_json("https://example.com/secret")
        self.assertNotIn("secret",str(ctx.exception))

    def test_request_budget(self):
        http=JsonHttp(budget=0)
        with self.assertRaises(BudgetError):
            http.get_json("https://example.com")

    def test_telegram_retry_after(self):
        exc=HTTPError("https://example.com",429,"limit",{},BytesIO(b'{"parameters":{"retry_after":5}}'))
        sleeps=[]
        http=JsonHttp(opener=NS(open=Mock(side_effect=[exc,BytesIO(b'{"ok":true}')])),sleep=sleeps.append)
        http.get_json("https://example.com")
        self.assertIn(5,sleeps)

    def test_rpc_error_is_not_empty_result(self):
        response=NS(status_code=200,text=")]}'\n\n[[\"wrb.fr\",null,null,null,null,[13]]]")
        client=GuardedClient(Config(),session=NS(post=Mock(return_value=response)),sleep=lambda _:None)
        with self.assertRaisesRegex(ServiceError,"RPC"):
            client.post("https://www.google.com", "f.req=test")


if __name__ == "__main__":
    unittest.main()

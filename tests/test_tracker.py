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

    def test_search_filters_are_rechecked_on_every_itinerary(self):
        def flown_by(*codes, **kw):
            p = pair(**kw)
            p[0].legs[0].airline = NS(name=codes[0])
            p[1].legs[0].airline = NS(name=codes[-1])
            return p
        check = lambda config, pairs: [(q.price, q.airlines) for q in normalize_pairs(
            pairs, "FRA", "2026-10-15", "2026-10-29", "any", config, lambda _: "")]
        # Too many stops in either direction never pass a stop limit.
        one_stop = replace(Config(), max_stops=1)
        self.assertEqual(check(one_stop, [pair(out_stops=2, in_stops=0, price=500), pair(in_stops=1, price=600)]),
                         [(60000, "TG")])
        # An excluded airline on any leg drops the itinerary; the next cheapest is used.
        self.assertEqual(check(replace(Config(), airlines_exclude=("SU",)),
                               [flown_by("SU", "TG", price=500), flown_by("TG", price=600)]), [(60000, "TG")])
        # "Only these" keeps itineraries where a selected airline flies at least one leg.
        self.assertEqual(check(replace(Config(), airlines=("QR",)),
                               [flown_by("TG", price=500), flown_by("QR", "TG", price=600)]), [(60000, "QR, TG")])
        # Codes the library stores with a leading underscore are shown and compared without it.
        self.assertEqual(check(replace(Config(), airlines=("4U",)), [flown_by("_4U", price=600)]), [(60000, "4U")])


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


class NoNonstopProvider(DemoProvider):
    """Demo fixture whose nonstop route has no prices until `nonstop_prices` is set."""
    nonstop_prices = False
    fail_nonstop = False

    def fetch(self, batch):
        if batch.profile == "nonstop" and self.fail_nonstop:
            raise ServiceError("RPC error")
        rows = super().fetch(batch)
        if batch.profile == "nonstop" and not self.nonstop_prices:
            rows = {pair: None for pair in rows}
        return rows


class EmptyRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name,"demo")
        self.config = replace(Config(), origins=("FRA",), departure_start="2026-10-15", departure_end="2026-10-15", min_trip_days=14,max_trip_days=14)
        self.provider = NoNonstopProvider(self.config)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def run_scan(self, hours):
        summary = scan(self.config,self.store,self.provider,NOW+timedelta(hours=hours),True)
        return summary["calendar_queries_planned"], summary["calendar_queries_skipped"]

    def test_empty_route_is_searched_daily_until_it_has_prices(self):
        self.assertEqual(self.run_scan(0), (2, 0))    # Nonstop searched, no price found.
        self.assertEqual(self.run_scan(6), (1, 1))    # Skipped during the day.
        self.assertEqual(self.run_scan(18), (1, 1))
        self.assertEqual(self.run_scan(24), (2, 0))   # Daily recheck, still empty.
        self.provider.nonstop_prices = True
        self.assertEqual(self.run_scan(48), (2, 0))   # Recheck finds a price ...
        self.assertEqual(self.run_scan(54), (2, 0))   # ... so it is searched every run again.

    def test_failed_search_never_marks_a_route_empty(self):
        self.provider.fail_nonstop = True
        self.run_scan(0)
        self.provider.fail_nonstop = False
        self.assertEqual(self.run_scan(6), (2, 0))

    def test_changed_search_window_rechecks_immediately(self):
        self.run_scan(0)
        self.config = replace(self.config, departure_start="2026-10-16", departure_end="2026-10-16")
        self.provider = NoNonstopProvider(self.config)
        self.assertEqual(self.run_scan(6), (2, 0))


class PruneTests(unittest.TestCase):
    def test_old_observations_go_but_alert_history_stays(self):
        with tempfile.TemporaryDirectory() as directory, Store(directory) as store:
            q = Quote("FRA", "2026-10-20", "2026-11-03", "layover", 60000, 700, 700, 1, 1, "TG", "")
            for days in (200, 100, 40, 1):
                at, run = stamp(NOW - timedelta(days=days)), f"r{days}"
                store.db.execute("INSERT INTO runs VALUES(?,?,?,?,?)", (run, at, "s", "ok", "{}"))
                store.db.execute("INSERT INTO calendar VALUES(?,?,?,?,?,?,?,?)",
                                 (run, "s", at, "FRA", "2026-10-20", "2026-11-03", "any", 60000))
                store.db.execute("INSERT INTO quotes VALUES(?,?,?,?,?,?,?,?,?)",
                                 (run, "s", at, "FRA", "2026-10-20", "2026-11-03", "layover", 60000, json.dumps(q.to_dict())))
                store.enqueue(run, "trend", NOW - timedelta(days=days), "alert", [q], "s")
            store.db.commit()
            store.prune(NOW, 30)
            count = lambda table: store.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            # The date grid is kept for the comparison period, verified fares for 120 days.
            self.assertEqual(count("calendar"), 1)
            self.assertEqual(count("quotes"), 3)
            self.assertEqual((count("runs"), count("outbox"), count("alert_items")), (4, 4, 4))
            # A longer comparison period keeps more.
            store.prune(NOW, 365)
            self.assertEqual(count("quotes"), 3)


class WindowEndedTests(unittest.TestCase):
    def test_ended_window_is_announced_once_with_a_settings_link(self):
        with tempfile.TemporaryDirectory() as directory, Store(directory) as store:
            config = replace(Config(), departure_start="2026-10-20", departure_end="2026-10-23")
            after = datetime(2026, 10, 23, 6, tzinfo=timezone.utc)
            for hours in (0, 6):
                summary = scan(config, store, DemoProvider(config), after + timedelta(hours=hours))
                self.assertEqual(summary["status"], "expired")
            notices = store.db.execute("SELECT text FROM outbox WHERE kind='notice'").fetchall()
            self.assertEqual(len(notices), 1)
            self.assertTrue(notices[0][0].startswith("BKK search window ended\n"))
            self.assertIn("settings.html", notices[0][0])
            # A new window is announced again when it ends.
            later = replace(config, departure_start="2026-10-24", departure_end="2026-10-25")
            scan(later, store, DemoProvider(later), datetime(2026, 10, 26, tzinfo=timezone.utc))
            self.assertEqual(store.db.execute("SELECT COUNT(*) FROM outbox WHERE kind='notice'").fetchone()[0], 2)
            # Searches before the end send nothing of the kind.
            with tempfile.TemporaryDirectory() as other, Store(other, "demo") as demo:
                scan(config, demo, DemoProvider(config), NOW, True)
                self.assertIsNone(demo.db.execute("SELECT 1 FROM outbox WHERE kind='notice'").fetchone())


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

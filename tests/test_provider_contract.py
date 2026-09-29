from dataclasses import replace
from datetime import date, datetime, timedelta
from types import SimpleNamespace as NS
import threading
import time
import unittest
from unittest.mock import Mock, patch

from tracker.config import Config
from tracker.network import ServiceError, BudgetError, TransientSourceError
from tracker.planner import Batch
from tracker.provider import FreeProvider, GuardedClient, PrefetchDates
import json
from urllib.parse import parse_qs, urlsplit


class CalendarContractTests(unittest.TestCase):
    def setUp(self):
        self.provider = FreeProvider.__new__(FreeProvider)
        self.provider.config = Config()
        self.provider.dates = NS(search=Mock(return_value=[]))
        self.start = date.today() + timedelta(days=30)
        self.batch = Batch("FRA", "any", self.start, self.start + timedelta(days=1), 14)
        self.dep, self.ret = self.batch.pairs()[0]

    def test_filters_request_exact_round_trip_currency_and_duration(self):
        self.provider.fetch(self.batch)
        call = self.provider.dates.search.call_args
        filters = call.args[0]
        self.assertEqual(filters.max_duration, 1259)
        self.assertEqual(filters.duration, 14)
        self.assertEqual(filters.flight_segments[0].travel_date, self.dep)
        self.assertEqual(filters.flight_segments[1].travel_date, self.ret)
        self.assertEqual(call.kwargs["currency"], "EUR")
        self.assertEqual(filters.stops.name, "ANY")

    def test_no_data_is_unknown_not_zero(self):
        result = self.provider.fetch(self.batch)
        self.assertEqual(len(result), 2)
        self.assertTrue(all(value is None for value in result.values()))

    def test_missing_array_is_a_failure(self):
        self.provider.dates.search.return_value = None
        with self.assertRaises(ServiceError):
            self.provider.fetch(self.batch)

    def test_only_requested_dates_used(self):
        self.provider.dates.search.return_value = [
            NS(date=(datetime.fromisoformat(self.dep),datetime.fromisoformat(self.ret)), price=650.25, currency="EUR"),
            NS(date=(datetime.fromisoformat(self.dep),datetime.fromisoformat(self.ret)+timedelta(days=1)), price=1, currency="EUR")]
        result = self.provider.fetch(self.batch)
        self.assertEqual(result[(self.dep,self.ret)],65025)
        self.assertEqual(len(result),2)

    def test_currency_fail_closed(self):
        self.provider.dates.search.return_value = [
            NS(date=(datetime.fromisoformat(self.dep),datetime.fromisoformat(self.ret)), price=650, currency=None)]
        with self.assertRaises(ServiceError):
            self.provider.fetch(self.batch)

    def test_direct_profile(self):
        self.provider.fetch(replace(self.batch, profile="nonstop"))
        self.assertEqual(self.provider.dates.search.call_args.args[0].stops.name, "NON_STOP")


class ClientLimitsTests(unittest.TestCase):
    def test_exhausted_internal_error_is_typed_and_bounded(self):
        bad = NS(status_code=200, text=")]}'\n" + json.dumps([["wrb.fr", "LqxFAb", None, None, None, [13]]]))
        session = NS(post=Mock(return_value=bad))
        client = GuardedClient(Config(), session=session, sleep=Mock())
        with self.assertRaises(TransientSourceError):
            client.post("https://www.google.com", "")
        self.assertEqual(client.used, 3)

    def test_rpc_denials_never_become_transient_errors(self):
        for code in (7, 8, 16):
            bad = NS(status_code=200, text=")]}'\n" + json.dumps([["wrb.fr", "LqxFAb", None, None, None, [code]]]))
            session = NS(post=Mock(return_value=bad))
            client = GuardedClient(Config(), session=session, sleep=Mock())
            for _ in range(2):
                with self.assertRaises(BudgetError):
                    client.post("https://www.google.com", "")
            self.assertEqual(client.used, 1)

    def test_internal_rpc_error_backs_off_then_recovers(self):
        bad = NS(status_code=200,text=")]}'\n"+json.dumps([["wrb.fr","LqxFAb",None,None,None,[13]]]))
        good = NS(status_code=200,text=")]}'\n"+json.dumps([["wrb.fr","LqxFAb","[]"]]))
        session = NS(post=Mock(side_effect=[bad,good]))
        sleep = Mock()
        client = GuardedClient(Config(),session=session,sleep=sleep)
        self.assertIs(client.post("https://www.google.com",""),good)
        sleep.assert_any_call(3)
        self.assertEqual(client.used,2)

    def test_pacing_gap_overlaps_network_latency(self):
        good = NS(status_code=200, text=")]}'\n"+json.dumps([["wrb.fr","LqxFAb","[]"]]))
        clock = [100.0]

        def post(*args, **kwargs):
            clock[0] += latency
            return good
        sleeps = []

        def sleep(seconds):
            sleeps.append(seconds)
            clock[0] += seconds
        with patch("tracker.provider.time.monotonic", lambda: clock[0]):
            client = GuardedClient(Config(request_interval_seconds=0.5), session=NS(post=post), sleep=sleep)
            latency = 0.2
            client.post("https://www.google.com", "")
            client.post("https://www.google.com", "")
            self.assertAlmostEqual(sleeps.pop(), 0.3)
            latency = 2
            client.post("https://www.google.com", "")
            sleeps.clear()
            client.post("https://www.google.com", "")
        self.assertEqual(sleeps, [])  # A slow response already covered the gap.
        self.assertEqual(client.used, 4)

    def test_parallel_requests_are_bounded(self):
        good = NS(status_code=200, text=")]}'\n"+json.dumps([["wrb.fr","LqxFAb","[]"]]))
        lock, active, peak = threading.Lock(), [0], [0]

        def post(*args, **kwargs):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.05)
            with lock:
                active[0] -= 1
            return good
        client = GuardedClient(Config(request_interval_seconds=0, max_parallel_requests=2),
                               session=NS(post=post), sleep=lambda _: None)
        threads = [threading.Thread(target=client.post, args=("https://www.google.com", "")) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(peak[0], 2)
        self.assertEqual(client.used, 6)

    def test_internal_error_widens_pacing_only_until_recovery(self):
        bad = NS(status_code=200,text=")]}'\n"+json.dumps([["wrb.fr","LqxFAb",None,None,None,[13]]]))
        good = NS(status_code=200,text=")]}'\n"+json.dumps([["wrb.fr","LqxFAb","[]"]]))
        client = GuardedClient(Config(request_interval_seconds=0.5),
                               session=NS(post=Mock(side_effect=[bad,bad,good,good,good])), sleep=lambda _:None)
        client.post("https://www.google.com","")
        self.assertEqual(client.interval, 1.0)  # Doubled twice to 2.0, halved once on success.
        client.post("https://www.google.com","")
        client.post("https://www.google.com","")
        self.assertEqual(client.interval, 0.5)  # Never below the configured interval.

    def test_denial_stops_all_later_requests_in_run(self):
        session = NS(post=Mock(return_value=NS(status_code=429)))
        client = GuardedClient(Config(),session=session,sleep=lambda _:None)
        for _ in range(2):
            with self.assertRaises(BudgetError):client.post("https://www.google.com","")
        self.assertEqual(session.post.call_count,1)

    def test_prefetch_transport_preserves_filter_payload_and_locale(self):
        from fli.models import FlightSearchFilters
        from fli.search import SearchFlights
        start = date.today() + timedelta(days=30)
        provider = FreeProvider(Config())
        filters = FlightSearchFilters(**provider.common("FRA",start.isoformat(),
            (start+timedelta(days=14)).isoformat(),"nonstop"))
        reply = NS(status_code=200, text=")]}'\n"+json.dumps([["wrb.fr","LqxFAb",json.dumps([None,None,None,None])]]))
        session = NS(post=Mock(return_value=reply))
        client = GuardedClient(Config(), session=session, sleep=lambda _:None)
        client.post(SearchFlights.BASE_URL+"?curr=EUR&hl=en&gl=DE", "f.req="+filters.encode())
        call = session.post.call_args
        self.assertEqual(urlsplit(call.args[0]).path,"/_/FlightsFrontendUi/data/batchexecute")
        self.assertEqual(parse_qs(urlsplit(call.args[0]).query)["curr"],["EUR"])
        rpc = json.loads(call.kwargs["data"]["f.req"])[0][0]
        self.assertEqual(rpc[0],"LqxFAb")
        self.assertEqual(json.loads(rpc[1]),filters.format())
        provider.close()

    def test_deadline_stops_before_network(self):
        session = NS(post=Mock())
        client = GuardedClient(Config(), session=session, sleep=lambda _:None)
        client.deadline = 0
        with self.assertRaises(BudgetError):
            client.post("https://www.google.com", "")
        session.post.assert_not_called()

    def test_access_denial_is_not_retried(self):
        session = NS(post=Mock(return_value=NS(status_code=403)))
        client = GuardedClient(Config(), session=session, sleep=lambda _:None)
        with self.assertRaises(ServiceError):
            client.post("https://www.google.com", "")
        self.assertEqual(session.post.call_count,1)


class PrefetchDateTests(unittest.TestCase):
    def recovery_provider(self, outcomes):
        provider = FreeProvider(Config())
        self.addCleanup(provider.close)
        provider.http.sleep = Mock()
        provider.dates.shopping._fetch_flights = Mock(side_effect=outcomes)
        start = date.today() + timedelta(days=30)
        return provider, Batch("FRA", "any", start, start + timedelta(days=2), 15)

    def test_deferred_recovery_only_repeats_failed_date(self):
        offer = [NS(price=650, currency="EUR")]
        provider, batch = self.recovery_provider([offer, TransientSourceError("RPC 13"), offer, offer])
        result = provider.fetch(batch)
        self.assertEqual(list(result.values()), [65000] * 3)
        dates = [call.args[0].flight_segments[0].travel_date
                 for call in provider.dates.shopping._fetch_flights.call_args_list]
        expected = [dep for dep, _ in batch.pairs()]
        self.assertEqual(dates, expected + [expected[1]])
        provider.http.sleep.assert_called_once_with(30)
        self.assertEqual(provider.dates.recovered_dates, 1)

    def test_failed_recheck_is_not_an_empty_calendar_or_success(self):
        error = TransientSourceError("RPC 13")
        provider, batch = self.recovery_provider([[], error, [], error])
        with self.assertRaisesRegex(TransientSourceError, "persisted after deferred recheck"):
            provider.fetch(batch)
        self.assertEqual(provider.dates.shopping._fetch_flights.call_count, 4)
        self.assertEqual(provider.dates.recovered_dates, 0)

    def test_widespread_internal_errors_stop_without_deferred_pass(self):
        provider, batch = self.recovery_provider([TransientSourceError("RPC 13")] * 3)
        with self.assertRaisesRegex(TransientSourceError, "Multiple date searches"):
            provider.fetch(batch)
        self.assertEqual(provider.dates.shopping._fetch_flights.call_count, 3)
        provider.http.sleep.assert_not_called()

    def test_budget_or_parser_errors_abort_even_with_pending_recovery(self):
        for error in (BudgetError("pause"), ServiceError("format changed")):
            provider, batch = self.recovery_provider([TransientSourceError("RPC 13"), error])
            with self.assertRaises(type(error)):
                provider.fetch(batch)
            self.assertEqual(provider.dates.shopping._fetch_flights.call_count, 2)
            provider.http.sleep.assert_not_called()

    def test_recheck_still_respects_transport_budget(self):
        provider, batch = self.recovery_provider([TransientSourceError("RPC 13"), [], [], BudgetError("budget")])
        with self.assertRaises(BudgetError):
            provider.fetch(batch)
        self.assertEqual(provider.dates.recovered_dates, 0)

    def test_empty_recheck_stays_unknown_not_zero(self):
        provider, batch = self.recovery_provider([TransientSourceError("RPC 13"), [], [], []])
        self.assertEqual(list(provider.fetch(batch).values()), [None] * 3)
        self.assertEqual(provider.dates.recovered_dates, 1)

    def test_each_date_pair_is_searched_and_empty_remains_unknown(self):
        provider = FreeProvider(Config())
        from fli.models import DateSearchFilters
        start = date.today()+timedelta(days=30)
        end = start+timedelta(days=1)
        filters = DateSearchFilters(**provider.common("FRA",start.isoformat(),
            (start+timedelta(days=14)).isoformat(),"nonstop"),
            from_date=start.isoformat(),to_date=end.isoformat(),duration=14)
        fetch = Mock(side_effect=[[NS(price=700,currency="EUR"),NS(price=650,currency="EUR")],None])
        provider.dates.shopping._fetch_flights = fetch
        rows = provider.dates.search(filters,currency="EUR",language="en",country="DE")
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0].price,650)
        self.assertEqual(fetch.call_count,2)
        for i,call in enumerate(fetch.call_args_list):
            query = call.args[0]
            self.assertEqual(query.flight_segments[0].travel_date,(start+timedelta(days=i)).isoformat())
            self.assertEqual(query.flight_segments[1].travel_date,(start+timedelta(days=14+i)).isoformat())
            self.assertEqual(query.stops.name,"NON_STOP")
            self.assertEqual(query.max_duration,1259)
        self.assertEqual(filters.flight_segments[0].travel_date,start.isoformat())
        provider.close()

    def test_pinned_library_verification_has_working_research_link(self):
        # Regression: build_flight_booking_url exists on unreleased fli main,
        # but not in the pinned 0.9.0 package installed on Actions.
        from test_tracker import pair
        provider = FreeProvider(Config())
        start = date.today()+timedelta(days=30)
        end = start+timedelta(days=14)
        fixture = pair(800,900)
        fixture[0].legs[0].departure_datetime = datetime.combine(start,datetime.min.time())
        fixture[1].legs[0].departure_datetime = datetime.combine(end,datetime.min.time())
        provider.flights.search = Mock(return_value=[fixture])
        quotes = provider.verify("FRA",start.isoformat(),end.isoformat(),"any")
        self.assertTrue(quotes)
        self.assertIn("curr=EUR",quotes[0].link)
        provider.close()


class SearchCacheTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = f"{self.dir.name}/search-cache.json"
        self.start = date.today() + timedelta(days=30)
        self.end = self.start + timedelta(days=14)
        self.args = ("FRA", self.start.isoformat(), self.end.isoformat(), "any")

    def provider(self):
        provider = FreeProvider(Config(), cache_path=self.path)
        self.addCleanup(provider.close)
        return provider

    def fixture(self):
        from fli.models import Airline, Airport, FlightLeg, FlightResult
        def result(src, dst, day, price):
            leg = FlightLeg(airline=Airline.TG, flight_number="921", departure_airport=src, arrival_airport=dst,
                departure_datetime=datetime.combine(day, datetime.min.time()),
                arrival_datetime=datetime.combine(day, datetime.min.time()) + timedelta(hours=11), duration=660)
            return FlightResult(legs=[leg], price=price, currency="EUR", duration=660, stops=0, self_transfer=False)
        return (result(Airport.FRA, Airport.BKK, self.start, 500), result(Airport.BKK, Airport.FRA, self.end, 640))

    def scanned(self, run_id="run-1"):
        provider = self.provider()
        provider.flights.search = Mock(return_value=[self.fixture()])
        provider.flights._last_session_id = "session-1"
        self.assertTrue(provider.verify(*self.args))
        provider.save_search_cache(run_id)

    def test_renamed_library_session_field_skips_cache_but_not_scan(self):
        provider = self.provider()
        provider.flights.search = Mock(return_value=[self.fixture()])
        provider.flights.__dict__.pop("_last_session_id", None)
        self.assertTrue(provider.verify(*self.args))
        self.assertEqual(provider.searches, {})

    def test_worker_threads_get_their_own_search_state(self):
        provider = self.provider()
        seen = []
        worker = threading.Thread(target=lambda: seen.extend([provider.searcher(), provider.searcher()]))
        worker.start()
        worker.join()
        self.assertIs(provider.searcher(), provider.flights)
        self.assertIs(seen[0], seen[1])
        self.assertIsNot(seen[0], provider.flights)
        self.assertIs(seen[0].client, provider.http)

    def test_scan_search_round_trips_for_same_run(self):
        self.scanned()
        provider = self.provider()
        provider.load_search_cache("run-1")
        pairs, session = provider.searches[provider.search_key(*self.args)]
        self.assertEqual(session, "session-1")
        self.assertEqual(pairs, [self.fixture()])

    def test_other_run_stale_or_corrupt_cache_is_ignored(self):
        self.scanned()
        provider = self.provider()
        provider.load_search_cache("run-2")
        self.assertEqual(provider.searches, {})
        with patch("tracker.provider.time.time", return_value=time.time() + 7200):
            provider.load_search_cache("run-1")
        self.assertEqual(provider.searches, {})
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{broken")
        provider.load_search_cache("run-1")
        self.assertEqual(provider.searches, {})

    def test_baggage_reuses_cached_search_and_session(self):
        self.scanned()
        provider = self.provider()
        provider.load_search_cache("run-1")
        provider.flights.search = Mock()
        with patch("tracker.fare_baggage.booking_quotes", return_value=["offer"]) as booking:
            self.assertEqual(provider.baggage_offers(*self.args), ["offer"])
        provider.flights.search.assert_not_called()
        self.assertEqual(provider.flights._last_session_id, "session-1")
        self.assertEqual(booking.call_count, 1)
        self.assertEqual(provider.cache_stats, {"cached_searches": 1, "cache_fallbacks": 0})

    def test_empty_or_failed_cached_offers_fall_back_to_live_search(self):
        for outcome in ([], ServiceError("stale session")):
            provider = self.provider()
            provider.searches = {provider.search_key(*self.args): ([], "old")}
            provider.booking_offers = Mock(side_effect=[outcome, ["fresh"]])
            self.assertEqual(provider.baggage_offers(*self.args), ["fresh"])
            self.assertEqual(provider.booking_offers.call_args_list[1].args, self.args)  # Live search, no cache.
            self.assertEqual(provider.cache_stats["cache_fallbacks"], 1)

    def test_budget_errors_are_not_retried_live(self):
        provider = self.provider()
        provider.searches = {provider.search_key(*self.args): ([], "old")}
        provider.booking_offers = Mock(side_effect=BudgetError("budget"))
        with self.assertRaises(BudgetError):
            provider.baggage_offers(*self.args)
        self.assertEqual(provider.booking_offers.call_count, 1)


if __name__ == "__main__":
    unittest.main()

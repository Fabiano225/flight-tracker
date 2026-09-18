from dataclasses import replace
from datetime import date, datetime, timedelta
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock

from tracker.config import Config
from tracker.network import ServiceError, BudgetError
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


if __name__ == "__main__":
    unittest.main()

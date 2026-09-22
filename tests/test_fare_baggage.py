import base64
from dataclasses import replace
import json
from types import SimpleNamespace as NS
import unittest

from tracker.fare_baggage import allowance, public_baggage, covers, decode_booking_quotes, booking_quotes, SOURCE
from tracker.provider import Quote


def row(block, price=817, currency='EUR', vendor='THAI'):
    """Minimal synthetic envelope around bag arrays checked against live pages.

    No captured sessions, click links or search tokens are stored in fixtures.
    The price token contains only a currency field, not a booking credential.
    """
    result = [None]*22
    result[0], result[1] = 0, [['TG',vendor,None,True]]
    result[3] = [['TG','937'],['TG','936']]
    result[7] = [[None,price],base64.b64encode(b'\x1a\x05\x1a\x03'+currency.encode()).decode()]
    result[21] = [None]*8
    result[21][7] = block
    return result


def wire(*rows):
    return ")]}'\n"+json.dumps([['wrb.fr',None,json.dumps([None,[list(rows)]])]])


class FareBaggageTests(unittest.TestCase):
    def setUp(self):
        self.q=Quote('AMS','2026-10-15','2026-10-29','nonstop',81700,680,760,0,0,'TG','https://www.google.com/travel/flights','a'*64)
        self.pair=tuple(NS(legs=[NS(airline=NS(name='TG'),flight_number=n)]) for n in ('937','936'))

    def decode(self, *rows):
        return decode_booking_quotes(wire(*rows),self.pair,self.q)

    def test_observed_thai_included_for_both_directions(self):
        q=self.decode(row([None,None,1,None,1,False,[1,1]]))[0]
        self.assertEqual(q.price,81700)
        for profile in ('cabin','checked','both'):
            self.assertTrue(covers(q.baggage,profile))
        self.assertIsNone(q.baggage['checked']['kg'])

    def test_observed_condor_paid_and_oman_cabin_only(self):
        condor=self.decode(row([None,None,3,None,3,None,[0,0]]))[0]
        self.assertEqual(condor.baggage['cabin']['status'],'chargeable')
        self.assertFalse(covers(condor.baggage,'both'))
        oman=self.decode(row([None,None,4,None,1,False,[0,1]]))[0]
        self.assertTrue(covers(oman.baggage,'cabin'))
        self.assertFalse(covers(oman.baggage,'checked'))
        self.assertEqual(oman.baggage['checked']['pieces'],0)

    def test_unknown_shapes_enums_and_boolean_counts_fail_closed(self):
        for block in (None,[],[None]*7,[None,None,99,None,99,None,[1,1]],
                      [None,None,1,None,1,None,[True,True]],
                      [None,None,1,None,1,None,[0,0]]):
            with self.subTest(block=block):
                q=self.decode(row(block))[0]
                self.assertFalse(covers(q.baggage,'both'))
                self.assertFalse(covers(q.baggage,'checked'))

    def test_exact_flights_currency_positive_price_required(self):
        included=[None,None,1,None,1,False,[1,1]]
        other=row(included);other[3][1][1]='999'
        for bad in (other,row(included,currency='USD'),row(included,price=0)):
            self.assertEqual(self.decode(bad),[])

    def test_latest_vendor_observation_supersedes_earlier_quote(self):
        included=[None,None,1,None,1,False,[1,1]]
        got=self.decode(row(included,817),row(included,830))
        self.assertEqual(len(got),1)
        self.assertEqual(got[0].price,83000)

    def test_public_allowlist_drops_private_metadata_and_unsupported_weights(self):
        q=self.decode(row([None,None,1,None,1,False,[1,1]]))[0]
        q.baggage.update(token='never-export',booking_url='https://evil.example')
        q.baggage['cabin']['kg']=8
        safe=public_baggage(q.baggage)
        self.assertNotIn('never-export',json.dumps(safe))
        self.assertIsNone(safe['cabin']['kg'])
        self.assertIsNone(public_baggage(dict(q.baggage,vendor='<script>')))
        self.assertIsNone(public_baggage(dict(q.baggage,whole_trip=False)))
        self.assertFalse(covers(None,'both'))

    def test_client_restored_even_after_detail_error(self):
        previous=object()
        def fail(*args,**kwargs):raise RuntimeError('failed')
        search=NS(client=previous,get_booking_options=fail)
        with self.assertRaises(RuntimeError):booking_quotes(search,None,self.pair,None,self.q)
        self.assertIs(search.client,previous)


if __name__=='__main__':unittest.main()

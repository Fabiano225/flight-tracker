from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from scripts.build_site import export_data, build, ASSETS, GENERATED, render_page, date_range, euro_text
from tracker.config import Config, Settings
from tracker.provider import Quote
from tracker.store import Store, stamp

NOW = datetime(2026,9,20,12,tzinfo=timezone.utc)


class SiteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.store=Store(self.root/'state')
        self.config=Config()
        self.path=self.root/'state'/'history.sqlite3'
        self.q=Quote('FRA','2026-10-15','2026-10-29','layover',60000,900,950,1,1,'TG','https://evil.example/token')

    def tearDown(self):
        self.store.close();self.temp.cleanup()

    def record(self, name, at, price=None, status='ok', quote=None, scope=None):
        scope=scope or self.config.scope()
        summary=dict(calendar_queries_ok=48,calendar_queries_planned=48,verified_quotes=int(price is not None),
                     errors=['sensitive-error-value'] if status=='partial' else [],config={'secret':'not-public'})
        self.store.db.execute('INSERT INTO runs VALUES(?,?,?,?,?)',(name,stamp(at),scope,status,json.dumps(summary)))
        if price is not None:
            q=replace(quote or self.q,price=price)
            self.store.db.execute('INSERT INTO quotes VALUES(?,?,?,?,?,?,?,?,?)',
                (name,scope,stamp(at),q.origin,q.departure,q.return_date,q.category,q.price,json.dumps(q.to_dict())))
        self.store.db.commit()

    def data(self):
        return export_data(self.path,self.config,NOW)

    def test_current_offers_and_real_history(self):
        self.record('old',NOW-timedelta(hours=6),65000)
        self.record('new',NOW,60000)
        data=self.data();self.assertEqual(len(data['offers']),1)
        item=data['offers'][0]
        self.assertEqual(item['price'],60000)
        self.assertEqual([p['price'] for p in data['histories'][item['id']]],[65000,60000])
        self.assertEqual(data['scan']['at'],data['offers_as_of'])
        self.assertEqual(item['days'],14)

    def test_failed_scan_retains_old_data_with_distinct_timestamp(self):
        self.record('old',NOW-timedelta(hours=6),60000)
        self.record('new',NOW,None,'partial')
        data=self.data();self.assertEqual(data['scan']['status'],'partial')
        self.assertNotEqual(data['scan']['at'],data['offers_as_of'])
        self.assertEqual(data['scan']['issues'],1)

    def test_no_outbox_metadata_raw_errors_or_untrusted_links_exported(self):
        self.record('new',NOW,60000,'partial')
        self.store.set_meta('TELEGRAM_BOT_TOKEN','secret-token-value')
        self.store.enqueue('new','health',NOW,'private-message-value')
        self.store.db.commit()
        raw=json.dumps(self.data())
        for forbidden in ['secret-token-value','private-message-value','sensitive-error-value','evil.example','not-public','message_id','chat_id','outbox']:
            self.assertNotIn(forbidden,raw)
        self.assertTrue(self.data()['offers'][0]['link'].startswith('https://www.google.com/travel/flights?'))

    def test_demo_rejected(self):
        self.store.set_meta('mode','demo');self.store.db.commit()
        with self.assertRaisesRegex(ValueError,'Only live'):self.data()

    def test_scope_dates_duration_and_history_window_filtered(self):
        self.record('old',NOW-timedelta(days=31),50000)
        self.record('wrongscope',NOW,50000,scope='wrong')
        self.record('wrongdate',NOW,50000,quote=replace(self.q,departure='2026-10-13'))
        self.record('wrongduration',NOW,50000,quote=replace(self.q,return_date='2026-11-12'))
        self.record('longflight',NOW,50000,quote=replace(self.q,outbound_minutes=1260))
        self.record('new',NOW,60000)
        data=self.data();self.assertEqual(len(data['offers']),1)
        self.assertEqual(len(next(iter(data['histories'].values()))),1)

    def test_october_14_departure_is_published(self):
        self.record('new', NOW, 60000, quote=replace(self.q, departure='2026-10-14'))
        data = self.data()
        self.assertEqual(data['config']['departure_start'], '2026-10-14')
        self.assertEqual(len(data['offers']), 1)
        self.assertEqual(data['offers'][0]['days'], 15)

    def test_empty_history_does_not_make_up_prices(self):
        data=self.data();self.assertEqual(data['offers'],[]);self.assertEqual(data['histories'],{})
        self.assertIsNone(data['scan'])

    def test_price_calendar_has_the_latest_date_search_within_the_trip(self):
        self.record('old',NOW-timedelta(hours=6),65000)
        self.record('new',NOW,60000)
        rows=[('old','FRA','2026-10-15','2026-10-29','any',55000),('new','FRA','2026-10-15','2026-10-29','any',58000),
              ('new','FRA','2026-10-15','2026-10-30','nonstop',None),('new','MUC','2026-10-15','2026-10-29','any',40000),
              ('new','FRA','2026-10-15','2026-11-20','any',41000)]
        for run,origin,dep,ret,profile,price in rows:
            at=NOW-timedelta(hours=6) if run=='old' else NOW
            self.store.db.execute('INSERT INTO calendar VALUES(?,?,?,?,?,?,?,?)',(run,self.config.scope(),stamp(at),origin,dep,ret,profile,price))
        self.store.db.commit()
        calendar=self.data()['calendar']
        self.assertEqual(calendar['at'],NOW.isoformat(timespec='seconds'))
        # Only the latest search; unknown prices stay null; other airports and dates are left out.
        self.assertEqual(calendar['rows'],[['FRA','BKK','2026-10-15',14,'any',58000],['FRA','BKK','2026-10-15',15,'nonstop',None]])

    def test_build_only_publishes_allowlisted_assets(self):
        self.record('new',NOW,60000,quote=replace(self.q,departure='2026-10-21',return_date='2026-11-04'))
        output=self.root/'public'
        build(self.path,output)
        self.assertEqual({p.name for p in output.iterdir()},set(ASSETS)|set(GENERATED))
        with self.assertRaisesRegex(ValueError,'empty'):build(self.path,output)
        form=json.loads((output/'search-config.json').read_text(encoding='utf-8'))
        self.assertEqual(set(form),{'version','repository','marker','primary_trip','trips','shared_fields','max_trips','max_destinations',
                                    'airlines','labels','travel_classes','limits'})
        self.assertEqual((form['version'],form['primary_trip'],form['max_trips']),(2,'main',5))
        self.assertEqual(form['airlines']['QR'],'Qatar Airways')
        self.assertEqual(form['trips'][0]['max_stops'],None)
        self.assertEqual(form['trips'][0]['destination'],'BKK')
        self.assertEqual(form['trips'][0]['id'],'main')
        self.assertNotIn('{{',(output/'index.html').read_text(encoding='utf-8'))
        data=json.loads((output/'data.json').read_text(encoding='utf-8'))
        self.assertEqual((data['version'],data['primary_trip'],len(data['trips'])),(2,'main',1))
        trip=data['trips'][0]
        self.assertEqual(trip['places']['BKK'],{'city':'Bangkok','country':'Thailand'})
        self.assertEqual(len(trip['offers']),1)
        self.assertEqual(trip['page']['city'],'Bangkok')
        self.assertNotIn('route_origins',trip['page'])

    def test_every_trip_is_published_with_the_primary_trip_first(self):
        bangkok=Config.load(Path(__file__).resolve().parents[1]/'config.json')
        amsterdam=replace(bangkok,id='ams',origins=('FRA',),destination='AMS',min_trip_days=3,max_trip_days=4,
                          display_names={'AMS':'Schiphol <Amsterdam>'})
        settings=Settings((bangkok,amsterdam),'ams')
        path=self.root/'multi.json'
        path.write_text(json.dumps(settings.file_dict()),encoding='utf-8')
        q=replace(self.q,origin='FRA',departure='2026-10-21',return_date='2026-10-24')
        self.record('bkk',NOW,60000,quote=replace(self.q,departure='2026-10-21',return_date='2026-11-04'),scope=bangkok.scope())
        self.record('ams',NOW,9000,quote=q,scope=amsterdam.scope())
        output=self.root/'public'
        build(self.path,output,path,NOW)
        data=json.loads((output/'data.json').read_text(encoding='utf-8'))
        self.assertEqual(data['primary_trip'],'ams')
        self.assertEqual([t['id'] for t in data['trips']],['ams','main'])
        self.assertEqual([[o['price'] for o in t['offers']] for t in data['trips']],[[9000],[60000]])
        self.assertEqual(data['trips'][0]['page']['city'],'Schiphol <Amsterdam>')
        self.assertEqual(data['trips'][0]['config']['destination'],'AMS')
        self.assertNotIn('version',data['trips'][0])
        # The page is prerendered for the primary trip; names are escaped.
        page=(output/'index.html').read_text(encoding='utf-8')
        self.assertIn('<title>Schiphol &lt;Amsterdam&gt; in view · Flightwatch</title>',page)
        self.assertIn('<span class="route-code">FRA</span>',page)
        form=json.loads((output/'search-config.json').read_text(encoding='utf-8'))
        self.assertEqual([t['id'] for t in form['trips']],['main','ams'])
        self.assertEqual(form['primary_trip'],'ams')

    def test_page_texts_follow_the_configured_route(self):
        template=(Path(__file__).resolve().parents[1]/'website'/'index.html').read_text(encoding='utf-8')
        page=render_page(template,Config.load(Path(__file__).resolve().parents[1]/'config.json'))
        for text in ('<title>Bangkok in view · Flightwatch</title>','YOUR PRICE RADAR FOR <span data-page="region_upper">THAILAND</span>',
                     'flights from Düsseldorf, Frankfurt and Amsterdam,',
                     '<span class="route-code">DUS</span><span class="route-code">FRA</span><span class="route-code">AMS</span>',
                     '<span data-page="city">Bangkok</span><span class="ticket-place" data-page="ticket_place">THAILAND / BKK</span>',
                     '20–23 Oct 2026','14–21 days','<span data-page="travellers">1 adult</span> · <span data-page="travel_class">Economy</span> · <span data-page="trip_kind">Round trip</span><span data-page="filter_summary"></span></p>',
                     'less than <span data-page="realert">€25</span> above',
                     'travel times <span data-page="duration_limit">under 21 hours</span> per direction',
                     '/ <span data-page="city">Bangkok</span> edition'):
            self.assertIn(text,page)
        tokyo=render_page(template,Config(origins=('MUC',),destination='HND',min_trip_days=7,max_trip_days=7,
            travel_class='business',max_direction_minutes=900,good_deal_layover_eur=900,realert_improvement_eur=12.5,
            max_stops=1,airlines=('NH','JL'),display_names={'HND':'Tokyo <Haneda> & "Co"'}))
        for text in ('Tokyo &lt;Haneda&gt; &amp; &quot;Co&quot; in view','>JAPAN</span>','JAPAN / HND',
                     'flights from Munich,','7 days','Business</span> · <span data-page="trip_kind">Round trip</span><span data-page="filter_summary"> · max. 1 stop · only NH, JL</span></p>',
                     'Separate targets','>€12.50</span> above','>up to 15 h 00 min</span> per direction'):
            self.assertIn(text,tokyo)
        self.assertNotIn('<Haneda>',tokyo)
        with self.assertRaisesRegex(ValueError,'placeholder'):
            render_page('{{unknown}}',self.config)

    def test_dates_and_amounts_in_english(self):
        self.assertEqual(date_range('2026-10-20','2026-10-20'),'20 Oct 2026')
        self.assertEqual(date_range('2026-10-28','2026-11-03'),'28 Oct – 3 Nov 2026')
        self.assertEqual(date_range('2026-12-28','2027-01-03'),'28 Dec 2026 – 3 Jan 2027')
        self.assertEqual(euro_text(1250),'€1,250')
        self.assertEqual(euro_text(1250.5),'€1,250.50')


if __name__=='__main__':unittest.main()

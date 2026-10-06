import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {searchAirports,searchAirlines,validate,validateTrips,requestEstimate,changedFields,shownValue,issueBody,issueUrl,issueTitle,
  tripIds,tripErrors,settingsJson,settingsChanges,MAX_URL,distanceKm,niceEuro,suggestPrices,observedPrices,alertAmounts,MIN_PRICES} from '../website/search-model.mjs';

const table=JSON.parse(readFileSync(new URL('../tracker/airports.json',import.meta.url),'utf8'));
const airlines=JSON.parse(readFileSync(new URL('../tracker/airlines.json',import.meta.url),'utf8')).airlines;
const file=JSON.parse(readFileSync(new URL('../config.json',import.meta.url),'utf8'));
const shared=['pending_ttl_hours','max_http_attempts_per_run','max_run_seconds','http_timeout_seconds','http_attempts','request_interval_seconds','max_parallel_requests'];
const main={...file,latest_return:null,max_stops:null,airlines:[],airlines_exclude:[],origin_targets:{},display_names:{},id:'main'};
const meta={trips:[main],primary_trip:'main',shared_fields:shared,max_trips:5,airlines,
  marker:'<!-- flightwatch-search-settings -->',repository:'Fabiano225/flight-tracker',
  labels:{destination:'Destination',origins:'Departure airports',max_stops:'Max. stops per direction',max_http_attempts_per_run:'Request budget per run',
    good_deal_layover_eur:'Price target, with stops (€)',primary_trip:'Shown first on the website',trips:'Trips'},
  travel_classes:{economy:'Economy',premium_economy:'Premium Economy',business:'Business',first_class:'First'},
  limits:{display_name:40,float:{drop_percent:[0.01,100],request_interval_seconds:[0,30]},
    int:{min_trip_days:[1,90],max_trip_days:[1,90],history_window_days:[1,365],max_deals_per_run:[1,6],pending_ttl_hours:[1,24],
      max_http_attempts_per_run:[1,2000],http_timeout_seconds:[1,120],http_attempts:[1,4],carry_on_bags:[0,1],checked_bags:[0,1],
      max_direction_minutes:[1,1259],max_verifications_per_run:[6,100],outbound_candidates:[1,10],max_run_seconds:[60,2400],max_parallel_requests:[1,6]}}};
const today='2026-10-01';
const config=changes=>({...main,...changes});
const amsterdam=config({origins:['FRA'],destination:'AMS',min_trip_days:3,max_trip_days:4,id:'ams'});

test('settings page script parses without executing DOM code',()=>{
  const source=readFileSync(new URL('../website/search.js',import.meta.url),'utf8');
  assert.doesNotThrow(()=>new Function(source.replace(/^import[^\n]+\n/,'')));
  assert.ok(!source.includes('innerHTML'));
});

test('airport search finds codes, English names and German aliases, ignoring accents',()=>{
  assert.equal(searchAirports(table,'bkk')[0].code,'BKK');
  assert.deepEqual(searchAirports(table,'tokyo').slice(0,2).map(a=>a.code),['HND','NRT']);
  assert.deepEqual(searchAirports(table,'tokio').slice(0,2).map(a=>a.code),['HND','NRT']);
  assert.equal(searchAirports(table,'munich')[0].code,'MUC');
  assert.equal(searchAirports(table,'münchen')[0].code,'MUC');
  assert.equal(searchAirports(table,'dusseldorf')[0].code,'DUS');
  assert.deepEqual(searchAirports(table,'  '),[]);
  assert.ok(searchAirports(table,'a').length<=8);
});

test('large airports come first among equally good matches',()=>{
  assert.deepEqual(searchAirports(table,'bang').slice(0,2).map(a=>a.code),['BKK','DMK']);
  assert.equal(searchAirports(table,'london')[0].code,'LHR');
  assert.equal(searchAirports(table,'paris')[0].code,'CDG');
  assert.equal(searchAirports(table,'frankfurt')[0].code,'FRA');
});

test('airline search finds codes and names',()=>{
  assert.equal(searchAirlines(airlines,'qr')[0].code,'QR');
  assert.equal(searchAirlines(airlines,'qatar')[0].code,'QR');
  assert.equal(searchAirlines(airlines,'lufthansa')[0].code,'LH');
  assert.deepEqual(searchAirlines(airlines,''),[]);
});

test('the current search is valid and its request estimate matches the tracker',()=>{
  const result=validate(main,meta,table,today);
  assert.deepEqual(result.errors,{});
  assert.deepEqual(result.estimate,{days:4,calendar:192,verification:72,requests:264,seconds:211});
  // Only departures from tomorrow on are searched; non-stop only skips the any-stops profile.
  assert.equal(requestEstimate(main,'2026-10-21').calendar,2*8*3*2);
  assert.equal(requestEstimate(main,'2026-10-23').calendar,0);
  assert.equal(requestEstimate(config({max_stops:0}),today).calendar,4*8*3);
  assert.equal(requestEstimate(config({max_stops:1}),today).calendar,4*8*3*2);
});

test('invalid settings are reported per field',()=>{
  const errors=c=>validate(config(c),meta,table,today).errors;
  assert.ok(errors({origins:[]}).origins);
  assert.ok(errors({origins:['XQZ']}).origins);
  assert.ok(errors({destination:'DUS'}).destination);
  assert.ok(errors({departure_end:'2026-10-19'}).departure_end);
  assert.ok(errors({departure_start:'2026-09-01',departure_end:'2026-10-01'}).departure_end);
  assert.ok(errors({departure_end:'2027-12-31'}).departure_end);
  assert.ok(errors({min_trip_days:22}).max_trip_days);
  assert.ok(errors({max_trip_days:14.5}).max_trip_days);
  assert.ok(errors({max_direction_minutes:1260}).max_direction_minutes);
  assert.ok(errors({request_interval_seconds:Number.NaN}).request_interval_seconds);
  assert.ok(errors({good_deal_nonstop_eur:0}).good_deal_nonstop_eur);
  assert.ok(errors({travel_class:'luxury'}).travel_class);
  assert.ok(errors({max_stops:3}).max_stops);
  assert.ok(errors({airlines:['FF']}).airlines);
  assert.ok(errors({airlines:['QR'],airlines_exclude:['QR']}).airlines_exclude);
  assert.ok(errors({airlines_exclude:Object.keys(airlines).slice(0,26)}).airlines_exclude);
  assert.ok(errors({display_names:{BKK:'Bang kok'}}).display_names);
  assert.ok(errors({display_names:{BKK:'x'.repeat(41)}}).display_names);
  assert.deepEqual(errors({display_names:{BKK:'Krung Thep'},max_stops:1,airlines:['QR','EK'],airlines_exclude:['SU']}),{});
});

test('searches beyond the request or time budget are blocked, near-limit ones warned',()=>{
  assert.match(validate(config({departure_end:'2026-12-31'}),meta,table,today).errors.estimate,/Too many requests/);
  assert.match(validate(config({request_interval_seconds:30}),meta,table,today).errors.estimate,/Search too long/);
  const near=validate(config({max_http_attempts_per_run:300}),meta,table,today);
  assert.deepEqual(near.errors,{});
  assert.equal(near.warnings.length,1);
});

test('filters are shown in plain words',()=>{
  assert.equal(shownValue('max_stops',null,meta),'any');
  assert.equal(shownValue('max_stops',0,meta),'non-stop only');
  assert.equal(shownValue('max_stops',2,meta),'up to 2');
  assert.equal(shownValue('airlines',[],meta),'all');
  assert.equal(shownValue('airlines_exclude',[],meta),'none');
  assert.equal(shownValue('airlines',['QR','EK'],meta),'QR, EK');
});

test('one trip: the issue carries the marker, the settings and a readable summary',()=>{
  const next=config({origins:['DUS','MUC'],destination:'HND',max_stops:1,display_names:{HND:'Tokyo Haneda'}});
  assert.deepEqual(changedFields(main,next),['origins','destination','max_stops','display_names']);
  const changes=settingsChanges(meta,[{id:'main',config:next}],0);
  assert.deepEqual(changes.map(c=>`${c.label}: ${c.before} → ${c.after}`).slice(0,3),
    ['Departure airports: DUS, FRA, AMS → DUS, MUC','Destination: BKK → HND','Max. stops per direction: any → up to 1']);
  const settings=settingsJson(meta,[{id:'main',config:next}],0), body=issueBody(meta,settings,changes);
  assert.ok(body.startsWith(meta.marker+'\n'));
  assert.match(body,/- Destination: BKK → HND/);
  const json=JSON.parse(body.match(/```json\n([\s\S]*?)\n```/)[1]);
  assert.deepEqual(json,settings);
  assert.deepEqual(Object.keys(json),['primary_trip','trips',...shared]);
  assert.equal(json.trips[0].id,'main');
  assert.equal(json.trips[0].max_stops,1);
  assert.ok(!('airlines' in json.trips[0]) && !('max_run_seconds' in json.trips[0]));  // Defaults and shared settings left out.
  assert.equal(issueTitle(settings),'Change search: DUS, MUC → HND');
  const url=new URL(issueUrl(meta,settings,changes));
  assert.equal(url.origin+url.pathname,'https://github.com/Fabiano225/flight-tracker/issues/new');
  assert.equal(url.searchParams.get('body'),body);
  assert.ok(url.href.length<MAX_URL);
  assert.equal(new URL(issueUrl(meta,settings,changes,false)).searchParams.get('body'),null);
});

test('new trips get ids from their destination; saved trips keep theirs',()=>{
  const trip=(destination,id=null)=>({id,config:config({destination})});
  assert.deepEqual(tripIds([trip('BKK','main'),trip('AMS'),trip('AMS'),trip(''),trip('SYD','syd')]),['main','ams','ams-2','trip','syd']);
  assert.deepEqual(tripIds([trip('SYD'),trip('SYD','syd')]),['syd-2','syd']);
});

test('trips share one request budget; field errors stay with their trip',()=>{
  const each=config({departure_end:'2026-11-25',max_trip_days:17});
  const result=validateTrips([each,{...each,id:'second',destination:'HND'}],meta,table,today);
  assert.ok(result.estimate.trips.every(e=>e.requests<1600));
  assert.match(result.errors.estimate,/Too many requests: about \d+ per run for all 2 trips together/);
  const broken=validateTrips([main,{...amsterdam,destination:'',max_run_seconds:30}],meta,table,today);
  assert.deepEqual(Object.keys(broken.trips[0]),[]);
  assert.deepEqual(Object.keys(broken.trips[1]),['destination']);
  assert.deepEqual(Object.keys(broken.errors),[]);  // Shared values come from the first trip.
  const shared=validateTrips([{...main,max_run_seconds:30},{...amsterdam,max_run_seconds:30}],meta,table,today);
  assert.deepEqual(Object.keys(shared.errors),['max_run_seconds']);
  assert.deepEqual(shared.trips,[{},{}]);
  const fine=validateTrips([main,amsterdam],meta,table,today);
  assert.equal(fine.estimate.requests,requestEstimate(main,today).requests+requestEstimate(amsterdam,today).requests);
});

test('several trips: changes name the trip and the issue holds all trips',()=>{
  const two={...meta,trips:[main,amsterdam]};
  const edited=[{id:'main',config:{...main,max_http_attempts_per_run:1700}},
    {id:'ams',config:{...amsterdam,good_deal_layover_eur:90,max_http_attempts_per_run:1700}},
    {id:null,config:{...amsterdam,id:'ams',destination:'LIS',max_http_attempts_per_run:1700}}];
  const changes=settingsChanges(two,edited,1).map(c=>`${c.label}: ${c.before} → ${c.after}`);
  assert.deepEqual(changes,['Request budget per run: 1600 → 1700','Shown first on the website: BKK → AMS',
    'AMS · Price target, with stops (€): 650 → 90','Trips: — → added: FRA → LIS, 2026-10-20 to 2026-10-23, 3–4 days']);
  assert.deepEqual(settingsChanges(two,[edited[0]],0).map(c=>c.after).slice(-1),['removed']);
  const settings=settingsJson(two,edited,1);
  assert.deepEqual(settings.trips.map(t=>t.id),['main','ams','lis']);
  assert.equal(settings.primary_trip,'ams');
  assert.equal(settings.max_http_attempts_per_run,1700);
  assert.equal(issueTitle(settings),'Change search: 3 trips (BKK, AMS, LIS)');
  assert.match(issueBody(two,settings,settingsChanges(two,edited,1)),/- FRA → AMS · departures 2026-10-20 to 2026-10-23 · 3–4 days · shown first/);
});

test('five full trips still fit into the issue link',()=>{
  const names={BKK:'Bangkok Suvarnabhumi',AMS:'Amsterdam Schiphol',SYD:'Sydney Kingsford Smith',JFK:'New York JFK',HND:'Tokyo Haneda'};
  const trips=Object.keys(names).map(code=>({id:null,config:config({destination:code,airlines_exclude:['SU','FR'],max_stops:1,
    display_names:{[code]:names[code]},good_deal_nonstop_eur:1234.5,good_deal_layover_eur:999.99})}));
  const settings=settingsJson(meta,trips,0), changes=settingsChanges(meta,trips,0);
  assert.ok(issueUrl(meta,settings,changes).length<MAX_URL);
});

test('price suggestions start from the flight distance',()=>{
  assert.equal(distanceKm(table,'FRA','FRA'),0);
  assert.ok(Math.abs(distanceKm(table,'FRA','BKK')-9000)<50);
  assert.equal(distanceKm(table,'FRA','XQZ'),null);
  assert.deepEqual([niceEuro(3),niceEuro(87),niceEuro(652),niceEuro(1234)],[5,85,650,1250]);
  assert.deepEqual(alertAmounts(650),{realert:25,drop:50});
  const bangkok=suggestPrices(config({origins:['FRA']}),table);
  assert.equal(bangkok.source,'estimate');
  assert.deepEqual(bangkok.values,{good_deal_nonstop_eur:750,good_deal_layover_eur:650,realert_improvement_eur:25,drop_eur:50});
  const amsterdam=suggestPrices(config({origins:['FRA'],destination:'AMS'}),table).values;
  assert.equal(amsterdam.good_deal_layover_eur,amsterdam.good_deal_nonstop_eur);  // Short trips: no non-stop premium.
  assert.ok(amsterdam.good_deal_layover_eur<150);
  const business=suggestPrices(config({origins:['FRA'],travel_class:'business'}),table).values;
  assert.ok(business.good_deal_layover_eur>3*bangkok.values.good_deal_layover_eur);
  assert.equal(suggestPrices(config({destination:''}),table),null);
});

test('checked prices give targets per airport where airports differ',()=>{
  const now=Date.parse('2026-10-01T12:00:00Z'), at=days=>new Date(now-days*86400000).toISOString();
  const points=(base,step)=>Array.from({length:10},(_,i)=>({at:at(i),price:(base+i*step)*100}));
  const trip={config:{history_window_days:30},
    offers:[{id:'a',origin:'AMS',category:'layover'},{id:'f',origin:'FRA',category:'layover'},
      {id:'d',origin:'DUS',category:'layover'},{id:'n',origin:'FRA',category:'nonstop'},{id:'o',origin:'AMS',category:'nonstop'}],
    histories:{a:[...points(500,2),{at:at(40),price:1000}],f:points(520,4),d:points(680,10),n:points(610,1),o:[{at:at(1),price:82000}]}};
  const observed=observedPrices(trip,now);
  assert.equal(observed.byOrigin.AMS.layover.length,10);  // The 40-day-old price is outside the comparison period.
  assert.equal(observed.layover.length,30);
  const suggestion=suggestPrices(config(),table,observed);
  assert.equal(suggestion.source,'prices');
  // A quarter of each airport's prices: AMS €500, FRA €530, DUS €700; too few AMS non-stop prices.
  assert.deepEqual(suggestion.airports,{DUS:{layover:700},FRA:{nonstop:610,layover:530},AMS:{layover:500}});
  // The trip's targets are the middle of the airports; only airports more than 5% away keep their own.
  assert.equal(suggestion.values.good_deal_layover_eur,530);
  assert.equal(suggestion.values.good_deal_nonstop_eur,610);
  assert.deepEqual(suggestion.values.origin_targets,{DUS:{layover:700},AMS:{layover:500}});
  assert.deepEqual(alertAmounts(530),{realert:20,drop:40});
  // Too few prices everywhere: only the rough guide, which leaves airport targets alone.
  const few={nonstop:[],layover:[],byOrigin:{AMS:{layover:observed.byOrigin.AMS.layover.slice(0,MIN_PRICES-1),nonstop:[]}}};
  const guide=suggestPrices(config(),table,few);
  assert.equal(guide.source,'estimate');
  assert.ok(!('origin_targets' in guide.values));
});

test('airport price targets are checked and shown in plain words',()=>{
  const errors=c=>tripErrors(config(c),meta,table,today).errors;
  assert.deepEqual(errors({origin_targets:{AMS:{layover:510},FRA:{nonstop:610,layover:530}}}),{});
  assert.ok(errors({origin_targets:{MUC:{layover:510}}}).origin_targets);
  assert.ok(errors({origin_targets:{AMS:{layover:0}}}).origin_targets);
  assert.ok(errors({origin_targets:{AMS:{layover:Number.NaN}}}).origin_targets);
  assert.equal(shownValue('origin_targets',{},meta),'same for all airports');
  assert.equal(shownValue('origin_targets',{FRA:{nonstop:610,layover:530},AMS:{layover:510}},meta),
    'AMS: with stops €510; FRA: non-stop €610, with stops €530');
});

test('a latest return date is checked and limits the date pairs',()=>{
  const errors=c=>tripErrors(config(c),meta,table,today).errors;
  assert.deepEqual(errors({latest_return:'2026-11-08',max_trip_days:19}),{});
  assert.ok(errors({latest_return:'2026-11-02'}).latest_return);  // 20 Oct + 14 days is 3 Nov.
  assert.ok(errors({latest_return:'8.11.2026'}).latest_return);
  // 20 Oct: 14–19 days, 21 Oct: 14–18 … 23 Oct: 14–16 days.
  assert.equal(requestEstimate(config({latest_return:'2026-11-08',max_trip_days:19}),today).calendar,(6+5+4+3)*3*2);
  assert.equal(shownValue('latest_return',null,meta),'none');
  const settings=settingsJson(meta,[{id:'main',config:config({latest_return:'2026-11-08',max_trip_days:19})}],0);
  assert.equal(settings.trips[0].latest_return,'2026-11-08');
  assert.ok(!('latest_return' in settingsJson(meta,[{id:'main',config:main}],0).trips[0]));
});

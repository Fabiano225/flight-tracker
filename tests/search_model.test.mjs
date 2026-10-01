import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {searchAirports,searchAirlines,validate,requestEstimate,changedFields,shownValue,issueBody,issueUrl,issueTitle,orderedConfig} from '../website/search-model.mjs';

const table=JSON.parse(readFileSync(new URL('../tracker/airports.json',import.meta.url),'utf8'));
const airlines=JSON.parse(readFileSync(new URL('../tracker/airlines.json',import.meta.url),'utf8')).airlines;
const file=JSON.parse(readFileSync(new URL('../config.json',import.meta.url),'utf8'));
const meta={config:{...file,max_stops:null,airlines:[],airlines_exclude:[],display_names:{}},airlines,
  marker:'<!-- flightwatch-search-settings -->',repository:'Fabiano225/flight-tracker',
  labels:{destination:'Destination',origins:'Departure airports',max_stops:'Max. stops per direction'},
  travel_classes:{economy:'Economy',premium_economy:'Premium Economy',business:'Business',first_class:'First'},
  limits:{display_name:40,float:{drop_percent:[0.01,100],request_interval_seconds:[0,30]},
    int:{min_trip_days:[1,90],max_trip_days:[1,90],history_window_days:[1,365],max_deals_per_run:[1,6],pending_ttl_hours:[1,24],
      max_http_attempts_per_run:[1,2000],http_timeout_seconds:[1,120],http_attempts:[1,4],carry_on_bags:[0,1],checked_bags:[0,1],
      max_direction_minutes:[1,1259],max_verifications_per_run:[6,100],outbound_candidates:[1,10],max_run_seconds:[60,2400],max_parallel_requests:[1,6]}}};
const today='2026-10-01';
const config=changes=>({...meta.config,...changes});

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
  const result=validate(meta.config,meta,table,today);
  assert.deepEqual(result.errors,{});
  assert.deepEqual(result.estimate,{days:4,calendar:192,verification:72,requests:264,seconds:211});
  // Only departures from tomorrow on are searched; non-stop only skips the any-stops profile.
  assert.equal(requestEstimate(meta.config,'2026-10-21').calendar,2*8*3*2);
  assert.equal(requestEstimate(meta.config,'2026-10-23').calendar,0);
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

test('issue carries the marker, every setting in file order and a readable summary',()=>{
  const next=config({origins:['DUS','MUC'],destination:'HND',max_stops:1,display_names:{HND:'Tokyo Haneda'}});
  assert.deepEqual(changedFields(meta.config,next),['origins','destination','max_stops','display_names']);
  const body=issueBody(meta,next);
  assert.ok(body.startsWith(meta.marker+'\n'));
  assert.match(body,/- Destination: BKK → HND/);
  assert.match(body,/- Max\. stops per direction: any → up to 1/);
  const json=body.match(/```json\n([\s\S]*?)\n```/)[1];
  assert.deepEqual(Object.keys(JSON.parse(json)),Object.keys(meta.config));
  assert.deepEqual(JSON.parse(json),orderedConfig(meta,next));
  assert.equal(issueTitle(next),'Change search: DUS, MUC → HND');
  const url=new URL(issueUrl(meta,next));
  assert.equal(url.origin+url.pathname,'https://github.com/Fabiano225/flight-tracker/issues/new');
  assert.equal(url.searchParams.get('body'),body);
  assert.ok(url.href.length<8000);
});

import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {filteredOffers,comparison,freshness,safeFlightLink,baggageView,matchingBase,baggageDescription,favoriteKey,favoriteOffers,readFavorites,writeFavorites,favoritesStorageKey} from '../website/model.mjs';
const offer={origin:'FRA',departure:'2026-10-15',days:14,category:'layover',price:60000,at:'2026-09-20T12:00:00+00:00'};
const config={good_deal_nonstop_eur:650,good_deal_layover_eur:650,realert_improvement_eur:25};
test('browser entry point parses without executing DOM code',()=>{
  const source=readFileSync(new URL('../website/app.js',import.meta.url),'utf8');
  assert.doesNotThrow(()=>new Function(source.replace(/^import[^\n]+\n/,'')));
  assert.ok(!source.includes('innerHTML'));
});
test('all filters combine and results sort by price',()=>{
  const offers=[offer,{...offer,origin:'AMS',price:50000},{...offer,category:'nonstop',days:21}];
  assert.equal(filteredOffers(offers,{}).length,3);
  assert.equal(filteredOffers(offers,{origin:'FRA',category:'layover',days:'14',departure:'2026-10-15'}).length,1);
  assert.equal(filteredOffers(offers,{origin:'DUS'}).length,0);
  assert.equal(filteredOffers(offers,{})[0].price,50000);
});
test('no invented previous price and current observation excluded from low',()=>{
  assert.equal(comparison(offer,[{at:offer.at,price:60000}],config).previous,null);
  const c=comparison(offer,[{at:'2026-09-20T06:00:00+00:00',price:70000},{at:offer.at,price:60000}],config);
  assert.equal(c.low,70000);assert.equal(c.delta,-10000);assert.equal(c.verdict,'Kauf prüfen');
});
test('budget classification and independent category thresholds',()=>{
  assert.equal(comparison({...offer,price:66000},[],config).verdict,'Beobachten');
  assert.equal(comparison(offer,[{at:'2026-09-19',price:55000}],config).verdict,'Im Budget, über Tief');
  assert.equal(comparison({...offer,category:'nonstop',price:70000},[],{...config,good_deal_nonstop_eur:800}).verdict,'Kauf prüfen');
});
test('stale and partial scans never look healthy',()=>{
  const data={scan:{at:offer.at,status:'partial'},offers_as_of:'2026-09-19T12:00:00+00:00'};
  assert.deepEqual(freshness(data,Date.parse('2026-09-21T12:00Z')),{stale:true,partial:true,fallback:true});
  assert.equal(freshness({},Date.now()).stale,true);
});
test('flight links reject script, wrong domain and protocol',()=>{
  for(const u of ['javascript:alert(1)','https://evil.example/travel/flights','http://www.google.com/travel/flights'])assert.equal(safeFlightLink(u),null);
  assert.ok(safeFlightLink('https://www.google.com/travel/flights?q=FRA'));
});
test('baggage switching never substitutes base prices and keeps histories separate',()=>{
  const root={config,offers:[offer],histories:{base:[]},baggage_profiles:{cabin:{offers:[{...offer,price:63000}],histories:{cabin:[]}}}};
  assert.equal(baggageView(root,'base'),root);
  assert.equal(baggageView(root,'cabin').offers[0].price,63000);
  assert.deepEqual(baggageView(root,'cabin').histories,{cabin:[]});
  assert.deepEqual(baggageView(root,'both').offers,[]);
});
test('base comparison requires exact itinerary and matching baseline timestamp',()=>{
  const q={...offer,itinerary_id:'abc',return_date:'2026-10-29'};
  const root={offers:[q]},view={base_at:q.at};
  assert.equal(matchingBase({...q,price:65000},root,view),q);
  assert.equal(matchingBase({...q,itinerary_id:null},root,view),null);
  assert.equal(matchingBase({...q,itinerary_id:'different'},root,view),null);
  assert.equal(matchingBase(q,root,{base_at:'yesterday'}),null);
});

test('baggage labels distinguish included paid absent and unknown without invented kg',()=>{
  assert.equal(baggageDescription(null,'cabin'),'Kabinenkoffer: keine Angabe');
  assert.equal(baggageDescription({cabin:{status:'chargeable'}},'cabin'),'Kabinenkoffer: gegen Aufpreis · Betrag unbekannt');
  assert.equal(baggageDescription({checked:{status:'not_included'}},'checked'),'Aufgabegepäck: nicht enthalten');
  assert.equal(baggageDescription({checked:{status:'included',pieces:1,kg:null}},'checked'),'Aufgabegepäck: 1 enthalten · kg: keine Angabe');
  assert.equal(baggageDescription({cabin:{status:'included',pieces:1,kg:8}},'cabin'),'Kabinenkoffer: 1 enthalten · 8 kg');
});

const savedOffer={...offer,return_date:'2026-10-29'};
test('favorite identity survives price airline and observation changes',()=>{
  assert.equal(favoriteKey(savedOffer,'BKK'),favoriteKey({...savedOffer,price:55000,airlines:'NEW',at:'tomorrow',itinerary_id:'new'},'BKK'));
  for(const changed of [{origin:'AMS'},{departure:'2026-10-14'},{return_date:'2026-10-30'},{category:'nonstop'}])
    assert.notEqual(favoriteKey(savedOffer,'BKK'),favoriteKey({...savedOffer,...changed},'BKK'));
  assert.notEqual(favoriteKey(savedOffer,'BKK'),favoriteKey(savedOffer,'HKT'));
  assert.notEqual(favoriteKey(savedOffer,'BKK','base'),favoriteKey(savedOffer,'BKK','both'));
});

test('favorites survive reload, removal persists, storage is namespaced',()=>{
  const memory=new Map([['unrelated','keep']]);
  const storage=()=>({getItem:k=>memory.get(k)??null,setItem:(k,v)=>memory.set(k,v)});
  assert.deepEqual([...readFavorites(storage).keys],[]);
  const keys=new Set([favoriteKey(savedOffer,'BKK')]);
  assert.equal(writeFavorites(keys,storage),true);
  assert.deepEqual(readFavorites(storage),{keys,ok:true});
  keys.clear();assert.equal(writeFavorites(keys,storage),true);
  assert.equal(readFavorites(storage).keys.size,0);
  assert.equal(memory.get('unrelated'),'keep');
  assert.ok(memory.has(favoritesStorageKey));
});

test('favorite filter combines with other filters and does not fabricate unavailable fares',()=>{
  const keys=new Set([favoriteKey(savedOffer,'BKK')]);
  const offers=[savedOffer,{...savedOffer,origin:'AMS',price:50000}];
  assert.deepEqual(filteredOffers(favoriteOffers(offers,keys,'BKK','base'),{origin:'FRA'}),[savedOffer]);
  assert.deepEqual(filteredOffers(favoriteOffers(offers,keys,'BKK','base'),{origin:'AMS'}),[]);
  assert.deepEqual(favoriteOffers(offers,keys,'BKK','both'),[]);
  assert.deepEqual(favoriteOffers([],keys,'BKK','base'),[]);
  assert.equal(keys.size,1); // A missing offer is not deleted.
});

test('blocked storage and malformed contents fail gracefully without changing in-memory favorites',()=>{
  const blocked=()=>{throw new Error('storage denied');};
  const keys=new Set([favoriteKey(savedOffer,'BKK')]);
  assert.deepEqual(readFavorites(blocked),{keys:new Set(),ok:false});
  assert.equal(writeFavorites(keys,blocked),false);assert.equal(keys.size,1);
  assert.equal(writeFavorites(keys,()=>({setItem:()=>{throw new Error('quota');}})),false);
  for(const raw of ['{bad','null','{}','[1]','["untrusted"]'])
    assert.equal(readFavorites(()=>({getItem:()=>raw})).ok,false);
  assert.equal(writeFavorites(new Set(['invalid']),()=>({setItem:()=>assert.fail()})),false);
});

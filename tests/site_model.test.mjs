import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {filteredOffers,comparison,freshness,safeFlightLink} from '../website/model.mjs';
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

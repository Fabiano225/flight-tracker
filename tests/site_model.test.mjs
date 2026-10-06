import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {filteredOffers,comparison,priceStatus,euro,shortDate,pruneFavorites,unavailableFavorites,freshness,safeFlightLink,baggageView,matchingBase,baggageDescription,favoriteKey,favoriteOffers,readFavorites,writeFavorites,favoritesStorageKey,chooseTrip,tripHref,flightLegs,timesFit,parseFavorite,belongsToTrip} from '../website/model.mjs';
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
  assert.equal(c.low,70000);assert.equal(c.delta,-10000);assert.equal(c.verdict,'Check to buy');
});
test('unchanged since the last check still reports an earlier, lower price',()=>{
  const history=[{at:'2026-09-18T06:00:00+00:00',price:58000},{at:'2026-09-19T06:00:00+00:00',price:60000},{at:offer.at,price:60000}];
  const c=comparison(offer,history,config);
  assert.equal(c.delta,0);assert.equal(c.low,58000);assert.equal(c.lowAt,'2026-09-18T06:00:00+00:00');
  const s=priceStatus(c);
  assert.equal(s.main,`↑ ${euro(2000)} above the low`);assert.equal(s.tone,'up');
  assert.equal(s.detail,`low ${euro(58000)} on ${shortDate('2026-09-18T06:00:00+00:00')} · unchanged since the last check`);
});
test('price status distinguishes new low, repeated low and first check',()=>{
  const at=p=>[{at:'2026-09-18T06:00:00+00:00',price:62000},{at:'2026-09-19T06:00:00+00:00',price:p}];
  const newLow=priceStatus(comparison(offer,at(61000),config));
  assert.equal(newLow.main,'↓ New low');assert.equal(newLow.tone,'down');
  assert.equal(newLow.detail,`previously ${euro(61000)} on ${shortDate('2026-09-19T06:00:00+00:00')} · since the last check ↓ ${euro(1000)}`);
  const repeated=priceStatus(comparison(offer,[{at:'2026-09-17T06:00:00+00:00',price:60000},{at:'2026-09-19T06:00:00+00:00',price:64000}],config));
  assert.equal(repeated.main,'At the low');
  assert.equal(repeated.detail,`as on ${shortDate('2026-09-17T06:00:00+00:00')} · since the last check ↓ ${euro(4000)}`);
  assert.deepEqual(priceStatus(comparison(offer,[],config)),{main:'First check',detail:null,tone:'neutral'});
});
test('favorites outside the search window are pruned, temporarily missing ones kept',()=>{
  const cfg={destination:'BKK',origins:['DUS','FRA','AMS'],departure_start:'2026-10-20',departure_end:'2026-10-23',min_trip_days:14,max_trip_days:21};
  const key=(origin,dep,ret,profile='base')=>JSON.stringify(['BKK',origin,dep,ret,'layover',profile]);
  const current=key('FRA','2026-10-20','2026-11-03'), oldWindow=key('FRA','2026-10-14','2026-10-28');
  const tooLong=key('FRA','2026-10-20','2026-11-12'), dropped=key('MUC','2026-10-20','2026-11-03');
  const result=pruneFavorites(new Set([current,oldWindow,tooLong,dropped]),cfg,'2026-09-29');
  assert.deepEqual([...result.keys],[current]);
  assert.deepEqual(result.removed.sort(),[oldWindow,tooLong,dropped].sort());
  assert.equal(pruneFavorites(new Set([current]),cfg,'2026-10-20').keys.size,0);  // Departure day reached.
});
test('favorites without a row are listed with a reason',()=>{
  const q={...offer,return_date:'2026-10-29'};
  const shown=favoriteKey(q,'BKK','base'), notChecked=JSON.stringify(['BKK','AMS','2026-10-16','2026-10-30','nonstop','base']);
  const otherBag=JSON.stringify(['BKK','DUS','2026-10-15','2026-10-29','layover','cabin']);
  const list=unavailableFavorites(new Set([shown,notChecked,otherBag]),[q],'BKK','base');
  assert.deepEqual(list.map(f=>[f.key,f.reason]),[[notChecked,'No checked offer at the moment'],[otherBag,'Saved with another baggage choice']]);
});
test('budget classification and independent category thresholds',()=>{
  assert.equal(comparison({...offer,price:66000},[],config).verdict,'Watch');
  assert.equal(comparison(offer,[{at:'2026-09-19',price:55000}],config).verdict,'Within budget, above low');
  assert.equal(comparison({...offer,category:'nonstop',price:70000},[],{...config,good_deal_nonstop_eur:800}).verdict,'Check to buy');
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
  assert.equal(baggageDescription(null,'cabin'),'Cabin bag: not stated');
  assert.equal(baggageDescription({cabin:{status:'chargeable'}},'cabin'),'Cabin bag: extra charge · amount unknown');
  assert.equal(baggageDescription({checked:{status:'not_included'}},'checked'),'Checked bag: not included');
  assert.equal(baggageDescription({checked:{status:'included',pieces:1,kg:null}},'checked'),'Checked bag: 1 included · kg: not stated');
  assert.equal(baggageDescription({cabin:{status:'included',pieces:1,kg:8}},'cabin'),'Cabin bag: 1 included · 8 kg');
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

test('the primary trip opens unless the address names another tracked trip',()=>{
  const site={primary_trip:'ams',trips:[{id:'ams'},{id:'main'}]};
  assert.deepEqual(chooseTrip(site,null),{trip:site.trips[0],unknown:false});
  assert.deepEqual(chooseTrip(site,'main'),{trip:site.trips[1],unknown:false});
  assert.deepEqual(chooseTrip(site,'gone'),{trip:site.trips[0],unknown:true});
  assert.equal(tripHref(site,'ams'),'./');
  assert.equal(tripHref(site,'main'),'./?trip=main');
  assert.equal(tripHref(site,'main','./settings.html'),'./settings.html?trip=main');
});
test('favorites of every trip are kept and listed only with their own trip',()=>{
  const bkk={destination:'BKK',origins:['FRA'],departure_start:'2026-10-20',departure_end:'2026-10-23',min_trip_days:14,max_trip_days:21};
  const ams={destination:'AMS',origins:['FRA'],departure_start:'2026-10-20',departure_end:'2026-10-23',min_trip_days:3,max_trip_days:4};
  const toBangkok=JSON.stringify(['BKK','FRA','2026-10-20','2026-11-03','layover','base']);
  const toAmsterdam=JSON.stringify(['AMS','FRA','2026-10-20','2026-10-23','nonstop','base']);
  const gone=JSON.stringify(['SYD','FRA','2026-10-20','2026-11-03','layover','base']);
  const result=pruneFavorites(new Set([toBangkok,toAmsterdam,gone]),[bkk,ams],'2026-10-01');
  assert.deepEqual([...result.keys],[toBangkok,toAmsterdam]);
  assert.deepEqual(result.removed,[gone]);
  assert.deepEqual(unavailableFavorites(result.keys,[],'AMS','base',ams).map(f=>f.key),[toAmsterdam]);
  assert.deepEqual(unavailableFavorites(result.keys,[],'BKK','base',bkk).map(f=>f.key),[toBangkok]);
});
test('the theme script applies the saved choice, also without browser storage',()=>{
  const source=readFileSync(new URL('../website/theme.js',import.meta.url),'utf8');
  const run=(stored,systemDark,storageFails=false)=>{
    const root={dataset:{},style:{}}, ignore=()=>{};
    const storage={getItem:()=>{if(storageFails)throw new Error('blocked');return stored;}};
    new Function('window','document','localStorage','matchMedia',source)({matchMedia:true,addEventListener:ignore},
      {documentElement:root,querySelectorAll:()=>[],addEventListener:ignore},storage,()=>({matches:systemDark,addEventListener:ignore}));
    return [root.dataset.themeChoice,root.dataset.theme,root.style.colorScheme];
  };
  assert.deepEqual(run(null,false),['auto','light','light']);
  assert.deepEqual(run(null,true),['auto','dark','dark']);
  assert.deepEqual(run('light',true),['light','light','light']);
  assert.deepEqual(run('dark',false),['dark','dark','dark']);
  assert.deepEqual(run('purple',true),['auto','dark','dark']);
  assert.deepEqual(run('dark',false,true),['auto','light','light']);
});
test('every page sets its theme before the styles load and offers all three choices',()=>{
  for(const name of ['index.html','settings.html']) {
    const html=readFileSync(new URL(`../website/${name}`,import.meta.url),'utf8');
    assert.ok(html.indexOf('src="./theme.js"')>0 && html.indexOf('src="./theme.js"')<html.indexOf('styles.css'),name);
    assert.deepEqual([...html.matchAll(/<option value="(auto|light|dark)">/g)].map(m=>m[1]),['auto','light','dark'],name);
  }
  // Dark rules follow the resolved theme only, so a saved choice always wins over the system.
  assert.ok(!readFileSync(new URL('../website/styles.css',import.meta.url),'utf8').includes('prefers-color-scheme'));
});
test('each flight is judged by the price target of its departure airport',()=>{
  const targets={...config,good_deal_nonstop_eur:650,origin_targets:{DUS:{layover:700}}};
  assert.equal(comparison({...offer,origin:'DUS',price:68000},[],targets).verdict,'Check to buy');
  assert.equal(comparison({...offer,origin:'DUS',price:68000},[],targets).threshold,70000);
  assert.equal(comparison({...offer,origin:'FRA',price:68000},[],targets).verdict,'Watch');
  assert.equal(comparison({...offer,origin:'DUS',category:'nonstop',price:68000},[],targets).threshold,65000);
});
test('favorites returning after the latest return date are removed',()=>{
  const cfg={destination:'BKK',origins:['FRA'],departure_start:'2026-10-20',departure_end:'2026-10-23',min_trip_days:14,max_trip_days:19,latest_return:'2026-11-08'};
  const key=(dep,ret)=>JSON.stringify(['BKK','FRA',dep,ret,'layover','base']);
  const result=pruneFavorites(new Set([key('2026-10-20','2026-11-08'),key('2026-10-21','2026-11-09')]),cfg,'2026-10-06');
  assert.deepEqual([...result.keys],[key('2026-10-20','2026-11-08')]);
});
test('flight times per direction: local times, day shift and a fallback without times',()=>{
  const q={outbound_minutes:1085,inbound_minutes:920,outbound_stops:1,inbound_stops:0,
    schedule:['2026-10-15T16:35','2026-10-16T12:40','2026-10-29T20:05','2026-10-29T23:30']};
  assert.deepEqual(flightLegs(q),[
    {label:'Out',minutes:1085,stops:1,departs:'16:35',arrives:'12:40',dayShift:1},
    {label:'Back',minutes:920,stops:0,departs:'20:05',arrives:'23:30',dayShift:0}]);
  // Westbound across the date line a flight can land on the previous day.
  assert.equal(flightLegs({...q,schedule:['2026-10-15T08:00','2026-10-14T22:00',...q.schedule.slice(2)]})[0].dayShift,-1);
  for(const schedule of [undefined,null,q.schedule.slice(0,3),[...q.schedule.slice(0,3),'23:30'],'2026-10-15T16:35'])
    assert.deepEqual(flightLegs({...q,schedule}),[{label:'Out',minutes:1085,stops:1},{label:'Back',minutes:920,stops:0}]);
  const legs=flightLegs({...q,layovers:[[['DOH',115]],[]]});
  assert.deepEqual(legs.map(leg=>leg.connections),[[{airport:'DOH',minutes:115}],[]]);
  assert.equal(flightLegs({...q,layovers:[[['DOH',115]]]})[0].connections,undefined);
});
test('time filters keep offers whose both flights leave and land inside the window',()=>{
  const q={...offer,outbound_minutes:1085,inbound_minutes:920,outbound_stops:1,inbound_stops:1,
    schedule:['2026-10-15T16:35','2026-10-16T12:40','2026-10-29T20:05','2026-10-30T06:25']};
  assert.ok(timesFit(q,'',''));
  assert.ok(timesFit(q,'8-24','6-24'));
  assert.ok(!timesFit(q,'8-20',''));  // The return leaves at 20:05.
  assert.ok(!timesFit(q,'','8-20'));  // It lands at 06:25.
  assert.ok(!timesFit({...q,schedule:null},'6-24',''));
  assert.ok(timesFit({...q,schedule:null},'',''));
  assert.deepEqual(filteredOffers([q,{...q,schedule:null}],{departs:'6-24'}).length,1);
});
test('trips with several destinations: offers, filters and favorites name their destination',()=>{
  const config={origins:['FRA'],destination:'BKK',destinations:['BKK','HKT'],departure_start:'2026-10-14',departure_end:'2026-10-20',min_trip_days:14,max_trip_days:14,latest_return:null};
  const phuket={...offer,destination:'HKT',return_date:'2026-10-29'};
  assert.equal(favoriteKey(phuket,'BKK'),JSON.stringify(['HKT','FRA','2026-10-15','2026-10-29','layover','base']));
  assert.equal(favoriteKey({...offer,return_date:'2026-10-29'},'BKK'),JSON.stringify(['BKK','FRA','2026-10-15','2026-10-29','layover','base']));
  const fav=parseFavorite(favoriteKey(phuket,'BKK'));
  assert.equal(belongsToTrip(fav,config),true);
  assert.equal(belongsToTrip(fav,{...config,destinations:['BKK']}),false);
  assert.deepEqual(filteredOffers([phuket,{...offer,destination:'BKK'}],{destination:'HKT'}),[phuket]);
});

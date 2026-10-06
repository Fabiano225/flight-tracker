export function filteredOffers(offers, filters) {
  const selectedAirlines=Array.isArray(filters.airlines)?filters.airlines:[];
  return offers.filter(q => (!filters.origin || q.origin === filters.origin)
    && (!filters.destination || q.destination === filters.destination)
    && (!filters.category || q.category === filters.category)
    && (!filters.departure || q.departure === filters.departure)
    && (!filters.days || q.days === Number(filters.days))
    && timesFit(q, filters.departs, filters.arrives)
    && (!selectedAirlines.length || (filters.airlineMode==='exclude'
      ? !airlineCodes(q).some(code=>selectedAirlines.includes(code))
      : airlineCodes(q).some(code=>selectedAirlines.includes(code)))))
    .sort((a,b) => a.price-b.price || a.departure.localeCompare(b.departure));
}

// Time filters ("6-24": from 06:00, before 24:00) apply to both flights, in local time.
// Offers without recorded times never match an active time filter.
export function timesFit(offer, departs, arrives) {
  if(!departs && !arrives)return true;
  const times=flightLegs(offer);
  if(!times[0].departs)return false;
  const inside=(time,window)=>{
    if(!window)return true;
    const [from,until]=window.split('-').map(Number), minutes=Number(time.slice(0,2))*60+Number(time.slice(3,5));
    return from*60<=minutes && minutes<until*60;
  };
  return times.every(leg=>inside(leg.departs,departs) && inside(leg.arrives,arrives));
}

export function airlineCodes(offer) {
  return [...new Set(String(offer?.airlines||'').split(',').map(code=>code.trim().toUpperCase()).filter(Boolean))];
}
export function airlineChoices(offers, names={}) {
  const counts=new Map();
  for(const offer of offers)for(const code of airlineCodes(offer))counts.set(code,(counts.get(code)||0)+1);
  return [...counts].map(([code,count])=>{const name=names[code];return {code,count,label:name?`${name} (${code})`:code};})
    .sort((a,b)=>a.label.localeCompare(b.label,'en'));
}

export const favoritesStorageKey = 'flightwatch:flight-tracker:favorites:v1';
// Track the same comparison as the chart, not a price/vendor that can change.
// An offer names its own destination (trips can have several); `destination` is the fallback.
export function favoriteKey(offer, destination, profile = 'base') {
  return JSON.stringify([offer.destination || destination, offer.origin, offer.departure, offer.return_date, offer.category, profile]);
}
function validFavoriteKey(key) {
  try {
    if(typeof key !== 'string' || key.length > 160)return false;
    const v=JSON.parse(key);
    return Array.isArray(v) && v.length===6 && v.every(x=>typeof x==='string')
      && /^[A-Z]{3}$/.test(v[0]) && /^[A-Z]{3}$/.test(v[1])
      && /^\d{4}-\d{2}-\d{2}$/.test(v[2]) && /^\d{4}-\d{2}-\d{2}$/.test(v[3])
      && ['nonstop','layover'].includes(v[4]) && ['base','cabin','checked','both'].includes(v[5]);
  } catch {return false;}
}
export function readFavorites(getStorage = ()=>globalThis.localStorage) {
  try {
    const raw=getStorage().getItem(favoritesStorageKey);
    if(raw===null)return {keys:new Set(),ok:true};
    const values=JSON.parse(raw);
    if(!Array.isArray(values) || !values.every(validFavoriteKey))throw new Error('Invalid favorites');
    return {keys:new Set(values),ok:true};
  } catch {return {keys:new Set(),ok:false};}
}
export function writeFavorites(keys, getStorage = ()=>globalThis.localStorage) {
  try {
    if(![...keys].every(validFavoriteKey))return false;
    getStorage().setItem(favoritesStorageKey,JSON.stringify([...keys]));
    return true;
  } catch {return false;}
}
export function favoriteOffers(offers, keys, destination, profile) {
  return offers.filter(q=>keys.has(favoriteKey(q,destination,profile)));
}
export function parseFavorite(key) {
  if(!validFavoriteKey(key))return null;
  const [destination,origin,departure,return_date,category,profile]=JSON.parse(key);
  return {key,destination,origin,departure,return_date,category,profile};
}
// Outside the configured search a favorite can never get a price again: the
// departure has passed, or its airport, dates or trip length are no longer searched.
export function favoriteExpired(fav, config, today) {
  const days=(Date.parse(fav.return_date)-Date.parse(fav.departure))/86400000;
  return !(config.destinations || [config.destination]).includes(fav.destination) || !config.origins.includes(fav.origin)
    || fav.departure<=today || fav.departure<config.departure_start || fav.departure>config.departure_end
    || days<config.min_trip_days || days>config.max_trip_days
    || Boolean(config.latest_return) && fav.return_date>config.latest_return;
}
// With several trips a favorite stays while any trip still searches it.
export function pruneFavorites(keys, configs, today) {
  const list=Array.isArray(configs)?configs:[configs], kept=new Set(), removed=[];
  for(const key of keys){const fav=parseFavorite(key);if(fav && list.some(config=>!favoriteExpired(fav,config,today)))kept.add(key);else removed.push(key);}
  return {keys:kept,removed};
}
export function belongsToTrip(fav, config) {
  return !favoriteExpired(fav,config,'');
}
// Still-searchable favorites without a row in this view: not checked in the
// latest run, or saved for another baggage selection. Listed so they can be removed.
// With a trip's config, favorites of other trips are left out.
export function unavailableFavorites(keys, offers, destination, profile, config=null) {
  const shown=new Set(offers.map(q=>favoriteKey(q,destination,profile)));
  return [...keys].filter(key=>!shown.has(key)).map(parseFavorite).filter(fav=>fav && (!config || belongsToTrip(fav,config)))
    .map(fav=>({...fav,reason:fav.profile===profile?'No checked offer at the moment':'Saved with another baggage choice'}))
    .sort((a,b)=>(a.profile!==profile)-(b.profile!==profile) || a.departure.localeCompare(b.departure) || a.return_date.localeCompare(b.return_date));
}
// Price target in euros: the departure airport's own one, if set, else the trip's.
export function targetFor(config, origin, category) {
  return config.origin_targets?.[origin]?.[category]
    ?? (category === 'nonstop' ? config.good_deal_nonstop_eur : config.good_deal_layover_eur);
}
export function comparison(offer, history, config) {
  const prior = history.filter(p => Date.parse(p.at) < Date.parse(offer.at)).sort((a,b)=>Date.parse(a.at)-Date.parse(b.at));
  const previous = prior.length ? prior[prior.length-1].price : null;
  const previousAt = prior.length ? prior[prior.length-1].at : null;
  const low = prior.length ? Math.min(...prior.map(p => p.price)) : null;
  // Most recent observation at the low: the most relevant "it was this cheap on ...".
  const lowAt = low === null ? null : prior.filter(p => p.price === low).at(-1).at;
  const threshold = 100 * targetFor(config, offer.origin, offer.category);
  const delta = previous === null ? null : offer.price - previous;
  const verdict = offer.price > threshold ? 'Watch'
    : low !== null && offer.price-low >= config.realert_improvement_eur*100 ? 'Within budget, above low' : 'Check to buy';
  return {previous, previousAt, low, lowAt, vsLow: low === null ? null : offer.price - low, delta,
    percent: previous === null ? null : delta/previous*100, threshold, verdict};
}

export const euro = value => new Intl.NumberFormat('en-GB', {style:'currency',currency:'EUR',minimumFractionDigits: value%100 ? 2 : 0,maximumFractionDigits: value%100 ? 2 : 0}).format(value/100);
export const shortDate = s => new Intl.DateTimeFormat('en-GB',{day:'numeric',month:'short',timeZone:'Europe/Berlin'}).format(new Date(s));

// Compare with the observed low of the history window first: "unchanged since
// the last check" alone hides that the same trip may have been cheaper days ago.
export function priceStatus(c) {
  if (c.low === null) return {main:'First check', detail:null, tone:'neutral'};
  const last = c.delta === 0 ? 'unchanged since the last check'
    : `since the last check ${c.delta < 0 ? '↓' : '↑'} ${euro(Math.abs(c.delta))}`;
  if (c.vsLow < 0) return {main:'↓ New low', detail:`previously ${euro(c.low)} on ${shortDate(c.lowAt)} · ${last}`, tone:'down'};
  if (c.vsLow === 0) return {main:'At the low', detail:`as on ${shortDate(c.lowAt)} · ${last}`, tone:'down'};
  return {main:`↑ ${euro(c.vsLow)} above the low`, detail:`low ${euro(c.low)} on ${shortDate(c.lowAt)} · ${last}`, tone:'up'};
}
export function freshness(data, now = Date.now()) {
  const at = Date.parse(data.scan?.at || '');
  const age = (now-at)/3600000;
  return {stale: !Number.isFinite(age) || age > 12,
    fallback: Boolean(data.offers_as_of && Date.parse(data.offers_as_of) !== Date.parse(data.scan?.at)),
    partial: Boolean(data.scan && data.scan.status !== 'ok')};
}
export function safeFlightLink(url) {
  try { const u = new URL(url); return u.protocol === 'https:' && u.hostname === 'www.google.com'
    && u.pathname === '/travel/flights' ? u.href : null; } catch { return null; }
}

export const baggageLabels = {base:'Base price', cabin:'1 cabin bag', checked:'1 checked bag', both:'Cabin bag + checked bag'};
export function baggageView(root, profile) {
  if(profile==='base')return root;
  const selected=root.baggage_profiles?.[profile];
  return {...(selected || {scan:null,offers_as_of:null,offers:[],histories:{}}),config:root.config,
    workflow_conclusion:root.workflow_conclusion};
}
export function matchingBase(offer, root, view) {
  if(!offer.itinerary_id || !view.base_at)return null;
  return root.offers.find(q=>q.itinerary_id===offer.itinerary_id && q.origin===offer.origin && q.destination===offer.destination
    && q.departure===offer.departure && q.return_date===offer.return_date && q.category===offer.category
    && q.at===view.base_at) || null;
}

export function baggageDescription(value, kind) {
  const title=kind==='cabin'?'Cabin bag':'Checked bag', bag=value?.[kind];
  if(!bag || !['included','chargeable','not_included'].includes(bag.status))return `${title}: not stated`;
  if(bag.status==='chargeable')return `${title}: extra charge · amount unknown`;
  if(bag.status==='not_included')return `${title}: not included`;
  const weight=typeof bag.kg==='number' && bag.kg>0?`${bag.kg} kg`:'kg: not stated';
  return `${title}: ${bag.pieces} included · ${weight}`;
}

// Several trips: the page opens the primary trip unless its address names another one.
export function chooseTrip(site, wanted) {
  const primary=site.trips.find(t=>t.id===site.primary_trip)||site.trips[0];
  const named=wanted?site.trips.find(t=>t.id===wanted):null;
  return {trip:named||primary, unknown:Boolean(wanted) && !named};
}
export function tripHref(site, id, page='./') {
  return id===site.primary_trip?page:`${page}?trip=${encodeURIComponent(id)}`;
}
// Both directions of an offer; with the local times at each airport when the search
// had them. dayShift counts calendar days from departure to arrival (+1: next day).
// `connections` lists [airport, minutes waiting] per stop when the search recorded them.
export function flightLegs(offer) {
  const times=Array.isArray(offer.schedule)&&offer.schedule.length===4&&offer.schedule.every(t=>/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(t))?offer.schedule:null;
  const stops=Array.isArray(offer.layovers)&&offer.layovers.length===2&&offer.layovers.every(Array.isArray)?offer.layovers:null;
  const dayNumber=t=>Date.parse(t.slice(0,10)+'T00:00:00Z')/864e5;
  return [['Out',0,offer.outbound_minutes,offer.outbound_stops],['Back',2,offer.inbound_minutes,offer.inbound_stops]].map(([label,i,minutes,count],n)=>({
    label,minutes,stops:count,
    ...(times?{departs:times[i].slice(11),arrives:times[i+1].slice(11),dayShift:dayNumber(times[i+1])-dayNumber(times[i])}:{}),
    ...(stops?{connections:stops[n].map(([airport,wait])=>({airport,minutes:wait}))}:{})}));
}
// Price calendar: the cheapest indicative date-search price (the return flight not yet
// chosen) per departure day and trip length, for the chosen airport, destination and
// connection. "With stops" uses the any-stops search, which can include non-stop flights.
export function calendarGrid(calendar, filters={}, today='') {
  const profile=filters.category==='nonstop'?'nonstop':'any', cells=new Map();
  for(const [origin,destination,departure,days,kind,price] of calendar?.rows||[]) {
    if(kind!==profile || departure<=today || (filters.origin && origin!==filters.origin)
      || (filters.destination && destination!==filters.destination))continue;
    const key=`${departure}|${days}`, cell=cells.get(key);
    if(!cell)cells.set(key,{departure,days,price,origin,destination});
    else if(price!==null && (cell.price===null || price<cell.price))Object.assign(cell,{price,origin,destination});
  }
  const values=[...cells.values()], prices=values.map(cell=>cell.price).filter(price=>price!==null);
  return {departures:[...new Set(values.map(cell=>cell.departure))].sort(),
    lengths:[...new Set(values.map(cell=>cell.days))].sort((a,b)=>a-b), cells,
    min:prices.length?Math.min(...prices):null, max:prices.length?Math.max(...prices):null};
}
// Five equal price bands between the cheapest (0) and the priciest (4) cell.
export function priceStep(price, min, max, steps=5) {
  if(price===null || min===null)return null;
  return max===min?0:Math.min(steps-1,Math.floor((price-min)/(max-min)*steps));
}
// Change against the earliest check of the same comparison within the last 7 days.
export function weekTrend(offer, history) {
  const until=Date.parse(offer.at), from=until-7*86400000;
  const earlier=history.filter(point=>{const at=Date.parse(point.at);return at>=from && at<until;})
    .sort((a,b)=>Date.parse(a.at)-Date.parse(b.at));
  return earlier.length?{delta:offer.price-earlier[0].price,since:earlier[0].at,full:Date.parse(earlier[0].at)-from<86400000}:null;
}
// The cheapest offer from each departure airport.
export function cheapestPerOrigin(offers, origins) {
  return origins.map(origin=>({origin,offer:offers.filter(q=>q.origin===origin).sort((a,b)=>a.price-b.price)[0]||null}));
}

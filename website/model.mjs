export function filteredOffers(offers, filters) {
  const selectedAirlines=Array.isArray(filters.airlines)?filters.airlines:[];
  return offers.filter(q => (!filters.origin || q.origin === filters.origin)
    && (!filters.category || q.category === filters.category)
    && (!filters.departure || q.departure === filters.departure)
    && (!filters.days || q.days === Number(filters.days))
    && (!selectedAirlines.length || (filters.airlineMode==='exclude'
      ? !airlineCodes(q).some(code=>selectedAirlines.includes(code))
      : airlineCodes(q).some(code=>selectedAirlines.includes(code)))))
    .sort((a,b) => a.price-b.price || a.departure.localeCompare(b.departure));
}

// Fallback when the published data has no name list (data.json `airlines`).
export const airlineNames={DE:'Condor',EY:'Etihad Airways',QR:'Qatar Airways',TG:'Thai Airways',WY:'Oman Air'};
export function airlineCodes(offer) {
  return [...new Set(String(offer?.airlines||'').split(',').map(code=>code.trim().toUpperCase()).filter(Boolean))];
}
export function airlineChoices(offers, names={}) {
  const counts=new Map();
  for(const offer of offers)for(const code of airlineCodes(offer))counts.set(code,(counts.get(code)||0)+1);
  return [...counts].map(([code,count])=>{const name=names[code]||airlineNames[code];return {code,count,label:name?`${name} (${code})`:code};})
    .sort((a,b)=>a.label.localeCompare(b.label,'en'));
}

export const favoritesStorageKey = 'flightwatch:flight-tracker:favorites:v1';
// Track the same comparison as the chart, not a price/vendor that can change.
export function favoriteKey(offer, destination, profile = 'base') {
  return JSON.stringify([destination, offer.origin, offer.departure, offer.return_date, offer.category, profile]);
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
  return fav.destination!==config.destination || !config.origins.includes(fav.origin)
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
  return root.offers.find(q=>q.itinerary_id===offer.itinerary_id && q.origin===offer.origin
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

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

export const airlineNames={DE:'Condor',EY:'Etihad Airways',QR:'Qatar Airways',TG:'Thai Airways',WY:'Oman Air'};
export function airlineCodes(offer) {
  return [...new Set(String(offer?.airlines||'').split(',').map(code=>code.trim().toUpperCase()).filter(Boolean))];
}
export function airlineChoices(offers) {
  const counts=new Map();
  for(const offer of offers)for(const code of airlineCodes(offer))counts.set(code,(counts.get(code)||0)+1);
  return [...counts].map(([code,count])=>({code,count,label:airlineNames[code]?`${airlineNames[code]} (${code})`:code}))
    .sort((a,b)=>a.label.localeCompare(b.label,'de'));
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
    || days<config.min_trip_days || days>config.max_trip_days;
}
export function pruneFavorites(keys, config, today) {
  const kept=new Set(), removed=[];
  for(const key of keys){const fav=parseFavorite(key);if(fav && !favoriteExpired(fav,config,today))kept.add(key);else removed.push(key);}
  return {keys:kept,removed};
}
// Still-searchable favorites without a row in this view: not checked in the
// latest run, or saved for another baggage selection. Listed so they can be removed.
export function unavailableFavorites(keys, offers, destination, profile) {
  const shown=new Set(offers.map(q=>favoriteKey(q,destination,profile)));
  return [...keys].filter(key=>!shown.has(key)).map(parseFavorite).filter(Boolean)
    .map(fav=>({...fav,reason:fav.profile===profile?'Derzeit kein geprüftes Angebot':'In anderer Gepäckauswahl gemerkt'}))
    .sort((a,b)=>(a.profile!==profile)-(b.profile!==profile) || a.departure.localeCompare(b.departure) || a.return_date.localeCompare(b.return_date));
}
export function comparison(offer, history, config) {
  const prior = history.filter(p => Date.parse(p.at) < Date.parse(offer.at)).sort((a,b)=>Date.parse(a.at)-Date.parse(b.at));
  const previous = prior.length ? prior[prior.length-1].price : null;
  const previousAt = prior.length ? prior[prior.length-1].at : null;
  const low = prior.length ? Math.min(...prior.map(p => p.price)) : null;
  // Most recent observation at the low: the most relevant "it was this cheap on ...".
  const lowAt = low === null ? null : prior.filter(p => p.price === low).at(-1).at;
  const threshold = 100 * (offer.category === 'nonstop' ? config.good_deal_nonstop_eur : config.good_deal_layover_eur);
  const delta = previous === null ? null : offer.price - previous;
  const verdict = offer.price > threshold ? 'Beobachten'
    : low !== null && offer.price-low >= config.realert_improvement_eur*100 ? 'Im Budget, über Tief' : 'Kauf prüfen';
  return {previous, previousAt, low, lowAt, vsLow: low === null ? null : offer.price - low, delta,
    percent: previous === null ? null : delta/previous*100, threshold, verdict};
}

export const euro = value => new Intl.NumberFormat('de-DE', {style:'currency',currency:'EUR',maximumFractionDigits: value%100 ? 2 : 0}).format(value/100);
export const shortDate = s => new Intl.DateTimeFormat('de-DE',{day:'2-digit',month:'2-digit',timeZone:'Europe/Berlin'}).format(new Date(s));

// Compare with the observed low of the history window first: "unchanged since
// the last check" alone hides that the same trip may have been cheaper days ago.
export function priceStatus(c) {
  if (c.low === null) return {main:'Erste Messung', detail:null, tone:'neutral'};
  const last = c.delta === 0 ? 'seit letzter Messung unverändert'
    : `seit letzter Messung ${c.delta < 0 ? '↓' : '↑'} ${euro(Math.abs(c.delta))}`;
  if (c.vsLow < 0) return {main:'↓ Neues Tief', detail:`bisher ${euro(c.low)} am ${shortDate(c.lowAt)} · ${last}`, tone:'down'};
  if (c.vsLow === 0) return {main:'Auf Tiefstpreis', detail:`wie am ${shortDate(c.lowAt)} · ${last}`, tone:'down'};
  return {main:`↑ ${euro(c.vsLow)} über Tief`, detail:`Tief ${euro(c.low)} am ${shortDate(c.lowAt)} · ${last}`, tone:'up'};
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

export const baggageLabels = {base:'Basispreis', cabin:'1 Kabinenkoffer', checked:'1 Aufgabegepäckstück', both:'Kabinenkoffer + Aufgabegepäck'};
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
  const title=kind==='cabin'?'Kabinenkoffer':'Aufgabegepäck', bag=value?.[kind];
  if(!bag || !['included','chargeable','not_included'].includes(bag.status))return `${title}: keine Angabe`;
  if(bag.status==='chargeable')return `${title}: gegen Aufpreis · Betrag unbekannt`;
  if(bag.status==='not_included')return `${title}: nicht enthalten`;
  const weight=typeof bag.kg==='number' && bag.kg>0?`${bag.kg} kg`:'kg: keine Angabe';
  return `${title}: ${bag.pieces} enthalten · ${weight}`;
}

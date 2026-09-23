export function filteredOffers(offers, filters) {
  return offers.filter(q => (!filters.origin || q.origin === filters.origin)
    && (!filters.category || q.category === filters.category)
    && (!filters.departure || q.departure === filters.departure)
    && (!filters.days || q.days === Number(filters.days)))
    .sort((a,b) => a.price-b.price || a.departure.localeCompare(b.departure));
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
export function comparison(offer, history, config) {
  const prior = history.filter(p => Date.parse(p.at) < Date.parse(offer.at)).sort((a,b)=>Date.parse(a.at)-Date.parse(b.at));
  const previous = prior.length ? prior[prior.length-1].price : null;
  const low = prior.length ? Math.min(...prior.map(p => p.price)) : null;
  const threshold = 100 * (offer.category === 'nonstop' ? config.good_deal_nonstop_eur : config.good_deal_layover_eur);
  const delta = previous === null ? null : offer.price - previous;
  const verdict = offer.price > threshold ? 'Beobachten'
    : low !== null && offer.price-low >= config.realert_improvement_eur*100 ? 'Im Budget, über Tief' : 'Kauf prüfen';
  return {previous, low, delta, percent: previous === null ? null : delta/previous*100, threshold, verdict};
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

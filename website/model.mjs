export function filteredOffers(offers, filters) {
  return offers.filter(q => (!filters.origin || q.origin === filters.origin)
    && (!filters.category || q.category === filters.category)
    && (!filters.departure || q.departure === filters.departure)
    && (!filters.days || q.days === Number(filters.days)))
    .sort((a,b) => a.price-b.price || a.departure.localeCompare(b.departure));
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

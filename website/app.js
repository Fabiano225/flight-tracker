import {filteredOffers, comparison, priceStatus, euro, freshness, safeFlightLink, baggageLabels, baggageView, matchingBase, baggageDescription, favoriteKey, favoriteOffers, readFavorites, writeFavorites, favoritesStorageKey, airlineChoices, pruneFavorites, unavailableFavorites, parseFavorite, belongsToTrip, chooseTrip, tripHref} from './model.mjs';

const $ = id => document.getElementById(id);
const day = s => new Intl.DateTimeFormat('en-GB',{day:'numeric',month:'short',timeZone:'Europe/Berlin'}).format(new Date(s+'T12:00:00Z'));
const when = s => new Intl.DateTimeFormat('en-GB',{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit',timeZone:'Europe/Berlin'}).format(new Date(s));
const category = q => q.category==='nonstop' ? 'Non-stop · both directions' : 'With stops';
const hours = m => `${Math.floor(m/60)} h ${String(m%60).padStart(2,'0')}`;
const node = (tag, text, cls) => {const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n;};
const svgNode = (tag, attrs, text) => {const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));if(text!==undefined)n.textContent=text;return n;};
let site, data, rootData, selectedId, tripId, tripNotice='';
let selectedAirlines=new Set(),airlineMode='include';
const savedFavorites=readFavorites();
let favorites=savedFavorites.keys, favoritesStored=savedFavorites.ok, prunedNotice='';
const berlinToday=()=>new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Berlin'}).format(new Date());
const airlineNames=()=>rootData?.airlines||{};
const wantedTrip=()=>new URLSearchParams(location.search).get('trip');

function showFavoritesStatus() {
  const profile=$('baggage').value;
  const available=data?favoriteOffers(data.offers,favorites,data.config.destination,profile).length:0;
  // With several trips, count the favorites of the trip shown.
  const saved=rootData?[...favorites].map(parseFavorite).filter(fav=>fav && belongsToTrip(fav,rootData.config)).length:favorites.size;
  $('favorites-count').textContent=`${saved} saved · ${available} available with this baggage choice`;
  $('favorites-status').textContent=[favoritesStored?'':'Browser storage is unavailable or the saved selection is damaged. Changes apply only to this open tab for now.',prunedNotice].filter(Boolean).join(' ');
  const missing=data?unavailableFavorites(favorites,data.offers,data.config.destination,profile,rootData.config):[], box=$('favorites-missing');
  box.replaceChildren();box.hidden=!missing.length;
  if(!missing.length)return;
  const list=node('ul');
  for(const fav of missing){
    const item=node('li'),text=node('span');
    text.append(node('strong',`${fav.origin} → ${fav.destination} · ${fav.category==='nonstop'?'Non-stop':'With stops'} · ${day(fav.departure)} – ${day(fav.return_date)}`),
      node('small',`${baggageLabels[fav.profile]} · ${fav.reason}`));
    const remove=node('button','Remove','chart-button');remove.type='button';
    remove.setAttribute('aria-label',`Remove favorite: ${fav.origin} to ${fav.destination}, ${fav.departure} to ${fav.return_date}, ${baggageLabels[fav.profile]}`);
    remove.addEventListener('click',()=>toggleFavorite(fav.key));
    item.append(text,remove);list.append(item);
  }
  box.append(node('p','Saved, but without an offer here:'),list);
}

function pruneExpiredFavorites() {
  if(!site)return;
  const pruned=pruneFavorites(favorites,site.trips.map(trip=>trip.config),berlinToday());
  if(!pruned.removed.length)return;
  favorites=pruned.keys;favoritesStored=writeFavorites(favorites);
  prunedNotice=`${pruned.removed.length===1?'1 favorite was':pruned.removed.length+' favorites were'} removed: the travel date, airport or trip length is no longer part of the search.`;
}

function toggleFavorite(key) {
  if(favorites.has(key))favorites.delete(key);else favorites.add(key);
  favoritesStored=writeFavorites(favorites);
  renderOffers();
  // Rendering replaces the row buttons; restore keyboard focus explicitly.
  const button=[...document.querySelectorAll('.favorite-button')].find(b=>b.dataset.favoriteKey===key);
  (button || $('favorites-only')).focus({preventScroll:true});
}

function activateBaggage() {
  const profile=$('baggage').value;
  data=baggageView(rootData,profile);
  renderAirlineOptions();
  $('baggage-note').textContent=profile==='base'
    ?'Base price without an extra baggage requirement. Bags may already be included.'
    :`Only offers whose booking details list ${baggageLabels[profile].toLowerCase()} as included for the whole trip. Price and history belong to this baggage choice.`;
  fillSelect('departure',[...new Set(data.offers.map(q=>q.departure))].sort(),day);
  showStatus();renderSummary();renderOffers();
}

function renderAirlineOptions() {
  const options=$('airline-options');options.replaceChildren();
  const allOffers=[...rootData.offers,...Object.values(rootData.baggage_profiles||{}).flatMap(view=>view.offers||[])];
  const choices=airlineChoices(allOffers,airlineNames()),currentCounts=new Map(airlineChoices(data.offers,airlineNames()).map(x=>[x.code,x.count]));
  for(const airline of choices) {
    const label=node('label',undefined,'airline-option');
    const input=node('input');input.type='checkbox';input.name='airline';input.value=airline.code;
    input.setAttribute('aria-label',airline.label);
    input.checked=selectedAirlines.has(airline.code);
    const name=node('span',airline.label,'airline-name');
    const count=node('span',String(currentCounts.get(airline.code)||0),'airline-count');
    label.append(input,name,count);options.append(label);
  }
  const none=options.children.length===0;
  $('airline-empty').hidden=!none;
  $('airline-choice-count').textContent=`${choices.length} airlines in the tracker · ${airlineChoices(data.offers,airlineNames()).length} available here`;
  $('airline-mode-include').checked=airlineMode==='include';
  $('airline-mode-exclude').checked=airlineMode==='exclude';
  updateAirlineSummary();
}

function updateAirlineSummary() {
  const count=selectedAirlines.size;
  $('airline-summary').textContent=count
    ?`${count} selected · ${airlineMode==='include'?'show only':'hide'}`
    :'All airlines';
  $('airline-clear').disabled=count===0;
}

function fillSelect(id, values, label) {
  const select=$(id), previous=select.value;
  while(select.options.length>1)select.remove(1);
  values.forEach(value=>{const option=node('option',label(value));option.value=value;select.append(option);});
  if([...select.options].some(o=>o.value===previous))select.value=previous;
}
function showStatus() {
  const fresh=freshness(data), scan=data.scan;
  const label=!scan?'No search data yet':scan.status==='expired'?'Travel window ended':fresh.stale?'Data not current':fresh.partial?'Search partly incomplete':'Latest search completed';
  $('status-label').replaceChildren(node('span','', 'dot'),node('span',label));
  $('last-check').textContent=scan?`Checked: ${when(scan.at)} · Berlin time`:'Waiting for the first search run';
  const messages=tripNotice?[tripNotice]:[];
  if($('baggage').value!=='base') {
    if(!scan)messages.push('There is no search data for this baggage choice yet. No base price is used as a baggage price.');
    else if(data.base_at!==rootData.offers_as_of)messages.push('The baggage check belongs to a different base data set, so no direct price difference is shown.');
  }
  if(!scan && $('baggage').value==='base')messages.push('No search has run for this trip yet. Searches run four times a day; prices appear about 10–40 minutes after one starts (see “View search runs” below).');
  if(data.workflow_conclusion && data.workflow_conclusion!=='success')messages.push('The latest tracker workflow did not succeed. The website shows the last stored data; details are under “View search runs”.');
  if(fresh.stale && scan)messages.push('The latest search is more than 12 hours old or not available yet. The prices shown are not live.');
  if(fresh.partial && scan.status!=='expired')messages.push(`The search was incomplete (${scan.batches_ok}/${scan.batches_planned} search blocks). Missing results do not mean “sold out” or “unchanged”.`);
  if(fresh.fallback)messages.push('Showing the last available prices because the newest search has no matching checked offers.');
  const expired=scan?.status==='expired';
  if(expired)messages.push('The configured departure window has ended. Only stored search prices are shown.');
  $('warning').replaceChildren(messages.join(' '));
  if(expired){const link=node('a','Set up a new search →','warning-link');link.href='./settings.html';$('warning').append(' ',link);}
  $('warning').hidden=!messages.length;
}
function renderSummary() {
  for(const type of ['layover','nonstop']) {
    const q=data.offers.filter(q=>q.category===type).sort((a,b)=>a.price-b.price)[0];
    $('best-'+type).textContent=q?euro(q.price):'—';
    $('best-'+type+'-info').textContent=q?`from ${q.origin} · ${q.days} days · ${day(q.departure)}`:'No checked offer in the latest prices';
  }
  const c=data.config;
  $('budget-value').textContent=c.good_deal_layover_eur===c.good_deal_nonstop_eur?euro(c.good_deal_layover_eur*100):'Separate targets';
  $('budget-note').textContent=c.good_deal_layover_eur===c.good_deal_nonstop_eur?'per person · round trip':`With stops ${euro(c.good_deal_layover_eur*100)} / non-stop ${euro(c.good_deal_nonstop_eur*100)}`;
  $('offer-count').textContent=data.offers.length;
  $('trip-dates').textContent=`${day(c.departure_start)} – ${day(c.departure_end)} ${c.departure_end.slice(0,4)}`;
  $('trip-days').textContent=c.min_trip_days===c.max_trip_days?`${c.min_trip_days} day${c.min_trip_days===1?'':'s'}`:`${c.min_trip_days}–${c.max_trip_days} days`;
  $('offer-timestamp').textContent=data.offers_as_of?`${baggageLabels[$('baggage').value]} · ${when(data.offers_as_of)} · Berlin time`:'No checked prices for this choice yet';
}
function renderOffers() {
  const filters=Object.fromEntries(new FormData($('filters'))), profile=$('baggage').value;
  filters.airlines=[...selectedAirlines];filters.airlineMode=airlineMode;
  const available=filters.favorites?favoriteOffers(data.offers,favorites,data.config.destination,profile):data.offers;
  const offers=filteredOffers(available,filters), body=$('offers-body');
  showFavoritesStatus();
  body.replaceChildren();
  $('results-count').textContent=`${offers.length} of ${data.offers.length} offers · sorted by price`;
  $('empty').hidden=offers.length>0;
  $('empty').textContent=data.offers.length?'No offers for this selection. Try another filter.':($('baggage').value==='base'?'No checked offers in the current search window yet. The search status is shown above.':'Baggage price unavailable: no checked offer lists the selected bags as included yet. That does not mean such fares do not exist.');
  if(filters.favorites)$('empty').textContent=favorites.size?'No available favorites for these filters and this baggage choice. Your saved selection is kept; missing offers are not current prices.':'No favorites yet. Turn off “Favorites only” and save offers with the star.';
  const select=$('history-select');select.replaceChildren();
  offers.forEach(q=>{const o=node('option',`${q.origin} · ${q.category==='nonstop'?'Non-stop':'With stops'} · ${day(q.departure)}–${day(q.return_date)} · ${q.days} days`);o.value=q.id;select.append(o);});
  if(!offers.some(q=>q.id===selectedId))selectedId=offers[0]?.id;
  select.value=selectedId||'';select.disabled=!offers.length;
  for(const q of offers) {
    const c=comparison(q,data.histories[q.id]||[],data.config), row=node('tr'); row.dataset.id=q.id;
    if(q.id===selectedId)row.className='selected-row';
    const route=node('td'),routeTitle=node('div',undefined,'route-title'),key=favoriteKey(q,data.config.destination,profile),marked=favorites.has(key);
    const star=node('button',marked?'★':'☆','favorite-button');star.type='button';star.dataset.favoriteKey=key;
    star.setAttribute('aria-pressed',String(marked));
    star.setAttribute('aria-label',`${marked?'Remove favorite':'Save as favorite'}: ${q.origin} to ${data.config.destination}, ${q.departure} to ${q.return_date}, ${category(q)}, ${baggageLabels[profile]}`);
    star.title=marked?'Remove favorite':'Save as favorite';star.addEventListener('click',()=>toggleFavorite(key));
    routeTitle.append(node('strong',`${q.origin} → ${data.config.destination}`),star);
    route.append(routeTitle,node('small',`${category(q)} · ${q.airlines}`));
    const dates=node('td');dates.append(node('strong',`${day(q.departure)} – ${day(q.return_date)}`),node('small',`${q.days} days · ${q.departure.slice(0,4)}`));
    const duration=node('td');duration.append(node('strong',`${hours(q.outbound_minutes)} / ${hours(q.inbound_minutes)}`),node('small',`Out / back · stops ${q.outbound_stops}/${q.inbound_stops}`));
    const price=node('td'),status=priceStatus(c);price.append(node('strong',euro(q.price),'price'),node('div',status.main,`delta ${status.tone}`));
    if(status.detail)price.append(node('small',status.detail,'delta-detail'));
    price.append(node('small',baggageDescription(q.baggage,'cabin'),'baggage-detail'));
    price.append(node('small',baggageDescription(q.baggage,'checked'),'baggage-detail'));
    if(q.baggage) {
      price.append(node('small',`Vendor: ${q.baggage.vendor} · Source: Google Flights booking details · round trip`,'baggage-detail'));
      if(q.baggage.checked_at)price.append(node('small',`Baggage checked: ${when(q.baggage.checked_at)}`,'baggage-detail'));
    }
    if($('baggage').value!=='base') {
      price.append(node('small','Selected bags included according to the offer details','baggage-included'));
      const base=matchingBase(q,rootData,data);
      if(base) {
        const delta=q.price-base.price;
        price.append(node('small',`Base price of the same flights: ${euro(base.price)} · offer difference ${delta>0?'+':''}${euro(delta)}`,'baggage-detail'));
        price.append(node('small','Possibly a different vendor or fare; not a separate baggage fee.','baggage-detail'));
      }
    }
    const verdict=node('td'), verdictLabel=$('baggage').value!=='base'?'Check fare':c.verdict;
    verdict.append(node('span',verdictLabel,`pill ${verdictLabel==='Check to buy'?'good':verdictLabel==='Watch'?'watch':'mid'}`));
    const actionCell=node('td'),actions=node('div',undefined,'actions'),button=node('button','History','chart-button');button.type='button';button.setAttribute('aria-label',`Price history ${q.origin}, ${q.departure} to ${q.return_date}, ${q.category==='nonstop'?'non-stop':'with stops'}`);
    button.addEventListener('click',()=>{selectedId=q.id;select.value=q.id;renderChart();$('history').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});select.focus({preventScroll:true});});actions.append(button);
    const url=safeFlightLink(q.link);if(url){const link=node('a','Find flight ↗','book-link');link.href=url;link.target='_blank';link.rel='noopener noreferrer';link.setAttribute('aria-label',`Find flight ${q.origin}, ${q.departure} on Google Flights`);actions.append(link);}
    actionCell.append(actions);row.append(route,dates,duration,price,verdict,actionCell);body.append(row);
  }
  renderChart();
}
function renderChart() {
  const q=data.offers.find(q=>q.id===selectedId), chart=$('chart');chart.replaceChildren();
  document.querySelectorAll('#offers-body tr').forEach(row=>row.classList.toggle('selected-row',row.dataset.id===selectedId));
  if(!q){$('chart-price').textContent='—';$('chart-change').textContent='Nothing selected';chart.append(node('p','No price observations for this filter yet.','empty'));$('chart-note').textContent='Choose another filter to see existing histories.';return;}
  const points=(data.histories[q.id]||[]).filter(p=>Date.parse(p.at)<=Date.parse(q.at)).sort((a,b)=>Date.parse(a.at)-Date.parse(b.at)), c=comparison(q,points,data.config);
  const status=priceStatus(c);
  $('chart-price').textContent=euro(q.price);$('chart-change').textContent=status.detail?`${status.main} · ${status.detail}`:status.main;
  $('chart-note').textContent=`${baggageLabels[$('baggage').value]} · ${points.length} checks${points.length?' since '+when(points[0].at):''}. ${c.low===null?'No earlier comparison price yet.':`Low of the ${data.config.history_window_days} days before this check: ${euro(c.low)} on ${when(c.lowAt)}.`} Only actually checked prices count. Same travel dates, flight type and baggage search; the airline may differ.`;
  if(!points.length){chart.append(node('p','No history available yet.','empty'));return;}
  const width=640,height=228,left=48,right=18,top=16,bottom=35;
  const values=points.map(p=>p.price).concat(c.threshold), min=Math.floor((Math.min(...values)-2000)/2500)*2500, max=Math.ceil((Math.max(...values)+2000)/2500)*2500;
  const times=points.map(p=>Date.parse(p.at)), start=times[0],end=times[times.length-1];
  const x=t=>end===start?(left+width-right)/2:left+(t-start)/(end-start)*(width-left-right),y=v=>top+(max-v)/(max-min)*(height-top-bottom);
  const svg=svgNode('svg',{viewBox:`0 0 ${width} ${height}`,role:'img','aria-label':`Price history ${q.origin}, ${q.departure} to ${q.return_date}: ${points.length} checks, currently ${euro(q.price)}`});
  for(let i=0;i<4;i++){const value=min+(max-min)*i/3,pos=y(value);svg.append(svgNode('line',{x1:left,y1:pos,x2:width-right,y2:pos,class:'chart-grid'}),svgNode('text',{x:left-9,y:pos+3,'text-anchor':'end',class:'chart-label'},'€'+Math.round(value/100)));}
  const coords=points.map((p,i)=>[x(times[i]),y(p.price)]);
  if(coords.length>1){svg.append(svgNode('path',{d:`M ${coords[0][0]} ${height-bottom} L `+coords.map(p=>p.join(' ')).join(' L ')+` L ${coords.at(-1)[0]} ${height-bottom} Z`,class:'chart-area'}));}
  svg.append(svgNode('line',{x1:left,x2:width-right,y1:y(c.threshold),y2:y(c.threshold),class:'chart-budget'}));
  if(coords.length>1)svg.append(svgNode('path',{d:'M '+coords.map(p=>p.join(' ')).join(' L '),class:'chart-line'}));
  coords.forEach(([cx,cy],i)=>{const dot=svgNode('circle',{cx,cy,r:4,class:'chart-point',tabindex:0,'aria-label':`${when(points[i].at)}: ${euro(points[i].price)}`});dot.append(svgNode('title',{},`${when(points[i].at)} · ${euro(points[i].price)}`));svg.append(dot);});
  [0,...(times.length>1?[times.length-1]:[])].forEach((i,j)=>svg.append(svgNode('text',{x:x(times[i]),y:height-8,'text-anchor':times.length===1?'middle':j?'end':'start',class:'chart-label'},when(points[i].at))));
  chart.append(svg);
}
// Texts that name the trip; the page arrives prerendered for the primary trip.
function applyPage(trip) {
  const page=trip.page;
  for(const element of document.querySelectorAll('[data-page]')){const value=page[element.dataset.page];if(typeof value==='string')element.textContent=value;}
  $('route-origins').replaceChildren(...trip.config.origins.map(code=>node('span',code,'route-code')));
  document.title=`${page.city} in view · Flightwatch`;
  document.querySelector('meta[name="description"]')?.setAttribute('content',`Your price radar for ${page.city}: flights from ${page.origin_cities}, with price history and a transparent search status.`);
  $('settings-link').href=tripHref(site,trip.id,'./settings.html');
}
function renderSwitcher() {
  const box=$('trip-switcher');box.replaceChildren();box.hidden=site.trips.length<2;
  for(const trip of box.hidden?[]:site.trips) {
    const link=node('a',undefined,'trip-tab');link.href=tripHref(site,trip.id);
    link.append(node('strong',trip.page.city),node('small',`${trip.page.code} · ${trip.page.trip_dates}`));
    if(trip.id===tripId)link.setAttribute('aria-current','page');
    link.addEventListener('click',event=>{
      if(event.button!==0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)return;
      event.preventDefault();
      if(trip.id===tripId)return;
      history.pushState(null,'',link.href);showTrip(trip.id);
    });
    box.append(link);
  }
}
function showTrip(wanted) {
  const {trip,unknown}=chooseTrip(site,wanted), changed=trip.id!==tripId;
  tripId=trip.id;tripNotice=unknown?`The trip “${wanted}” is not tracked any more, so ${trip.page.city} is shown.`:'';
  rootData={...trip,airlines:site.airlines,workflow_conclusion:site.workflow_conclusion};
  if(changed) {
    // Filters belong to one trip: airports, days and airlines differ between trips.
    $('filters').reset();selectedAirlines.clear();airlineMode='include';selectedId=null;
  }
  applyPage(trip);renderSwitcher();pruneExpiredFavorites();
  fillSelect('origin',trip.config.origins,v=>trip.places?.[v]?.city&&trip.places[v].city!==v?`${trip.places[v].city} (${v})`:v);
  fillSelect('days',Array.from({length:trip.config.max_trip_days-trip.config.min_trip_days+1},(_,i)=>trip.config.min_trip_days+i),v=>`${v} days`);
  activateBaggage();
  document.documentElement.removeAttribute('data-trip-loading');
}
async function load() {
  $('reload').disabled=true;
  try {
    const response=await fetch('./data.json',{cache:'no-store'});if(!response.ok)throw new Error('Fetch failed');
    const value=await response.json();
    if(value.version!==2 || !Array.isArray(value.trips) || !value.trips.length || !value.trips.every(t=>t.id && t.page && t.config && Array.isArray(t.offers) && t.histories))throw new Error('Invalid data');
    site=value;showTrip(wantedTrip());
  } catch {
    document.documentElement.removeAttribute('data-trip-loading');
    $('status-label').textContent='Loading data failed';$('warning').hidden=false;
    $('warning').textContent='The price data could not be loaded. Please refresh later or check the search runs on GitHub. Prices already shown may be out of date.';
    if(!data){$('results-count').textContent='No data loaded';$('empty').hidden=false;$('empty').textContent='No sample prices are shown.';}
  } finally {$('reload').disabled=false;}
}
$('filters').addEventListener('submit',event=>event.preventDefault());
$('filters').addEventListener('change',event=>{
  if(event.target.name==='airline') {
    if(event.target.checked)selectedAirlines.add(event.target.value);else selectedAirlines.delete(event.target.value);
    updateAirlineSummary();
  }
  if(event.target.name==='airline-mode') {
    airlineMode=event.target.value;updateAirlineSummary();
  }
  if(data)renderOffers();
});
$('airline-clear').addEventListener('click',()=>{
  selectedAirlines.clear();renderAirlineOptions();if(data)renderOffers();
});
$('filters').addEventListener('reset',()=>{setTimeout(()=>{selectedAirlines.clear();airlineMode='include';renderAirlineOptions();if(data)renderOffers();},0);});
$('history-select').addEventListener('change',event=>{selectedId=event.target.value;renderChart();});
$('reload').addEventListener('click',load);
window.addEventListener('popstate',()=>{if(site)showTrip(wantedTrip());});
$('baggage').addEventListener('change',()=>{if(rootData){selectedId=null;activateBaggage();}});
window.addEventListener('storage',event=>{
  if(event.key!==favoritesStorageKey && event.key!==null)return;
  const saved=readFavorites();favorites=saved.keys;favoritesStored=saved.ok;pruneExpiredFavorites();
  if(data)renderOffers();else showFavoritesStatus();
});
showFavoritesStatus();
load();

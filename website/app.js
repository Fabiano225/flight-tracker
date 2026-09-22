import {filteredOffers, comparison, freshness, safeFlightLink, baggageLabels, baggageView, matchingBase} from './model.mjs';

const $ = id => document.getElementById(id);
const euro = value => new Intl.NumberFormat('de-DE', {style:'currency',currency:'EUR',maximumFractionDigits: value%100 ? 2 : 0}).format(value/100);
const day = s => new Intl.DateTimeFormat('de-DE',{day:'2-digit',month:'short',timeZone:'Europe/Berlin'}).format(new Date(s+'T12:00:00Z'));
const when = s => new Intl.DateTimeFormat('de-DE',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit',timeZone:'Europe/Berlin'}).format(new Date(s))+' Uhr';
const category = q => q.category==='nonstop' ? 'Direkt · beide Richtungen' : 'Mit Umstieg';
const hours = m => `${Math.floor(m/60)} h ${String(m%60).padStart(2,'0')}`;
const node = (tag, text, cls) => {const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n;};
const svgNode = (tag, attrs, text) => {const n=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,String(v));if(text!==undefined)n.textContent=text;return n;};
let data, rootData, selectedId;

function activateBaggage() {
  const profile=$('baggage').value;
  data=baggageView(rootData,profile);
  $('baggage-note').textContent=profile==='base'
    ?'Basispreis ohne zusätzliche Gepäckanforderung. Gepäck kann bereits enthalten sein.'
    :`Angefragt: ${baggageLabels[profile]} für Hin- und Rückflug. Alle Preise, Sortierung und Verläufe unten beziehen sich auf diese Variante. Gewicht: keine Angabe.`;
  fillSelect('departure',[...new Set(data.offers.map(q=>q.departure))].sort(),day);
  showStatus();renderSummary();renderOffers();
}

function fillSelect(id, values, label) {
  const select=$(id), previous=select.value;
  while(select.options.length>1)select.remove(1);
  values.forEach(value=>{const option=node('option',label(value));option.value=value;select.append(option);});
  if([...select.options].some(o=>o.value===previous))select.value=previous;
}
function showStatus() {
  const fresh=freshness(data), scan=data.scan;
  const label=!scan?'Noch keine Suchdaten':scan.status==='expired'?'Reisefenster beendet':fresh.stale?'Datenstand nicht aktuell':fresh.partial?'Suche teilweise unvollständig':'Letzte Suche abgeschlossen';
  $('status-label').replaceChildren(node('span','', 'dot'),node('span',label));
  $('last-check').textContent=scan?`Geprüft: ${when(scan.at)} · Berlin`:'Warte auf ersten Suchlauf';
  const messages=[];
  if($('baggage').value!=='base') {
    if(!scan)messages.push('Für diese Gepäckvariante liegen noch keine Suchdaten vor. Es wird kein Basispreis als Gepäckpreis eingesetzt.');
    else if(data.base_at!==rootData.offers_as_of)messages.push('Die Gepäcksuche gehört zu einem anderen Basis-Datenstand. Ein direkter Preisaufschlag wird deshalb nicht angezeigt.');
  }
  if(data.workflow_conclusion && data.workflow_conclusion!=='success')messages.push('Der letzte Tracker-Workflow war nicht erfolgreich. Die Website zeigt den zuletzt gespeicherten Datenstand; Details stehen unter „Suchläufe ansehen“.');
  if(fresh.stale)messages.push('Die letzte Suche liegt mehr als 12 Stunden zurück oder ist noch nicht verfügbar. Die angezeigten Preise sind kein aktueller Live-Stand.');
  if(fresh.partial && scan.status!=='expired')messages.push(`Die Suche war nicht vollständig (${scan.batches_ok}/${scan.batches_planned} Suchblöcke). Fehlende Ergebnisse bedeuten nicht „ausgebucht“ oder „unverändert“.`);
  if(fresh.fallback)messages.push('Gezeigt wird der letzte verfügbare Preisstand, da die neueste Suche keine passenden geprüften Angebote enthält.');
  if(scan?.status==='expired')messages.push('Das konfigurierte Abflugfenster ist beendet. Hier siehst du nur gespeicherte Suchpreise.');
  $('warning').textContent=messages.join(' ');$('warning').hidden=!messages.length;
}
function renderSummary() {
  for(const type of ['layover','nonstop']) {
    const q=data.offers.filter(q=>q.category===type).sort((a,b)=>a.price-b.price)[0];
    $('best-'+type).textContent=q?euro(q.price):'—';
    $('best-'+type+'-info').textContent=q?`ab ${q.origin} · ${q.days} Tage · ${day(q.departure)}`:'Kein geprüftes Angebot im Preisstand';
  }
  const c=data.config;
  $('budget-value').textContent=c.good_deal_layover_eur===c.good_deal_nonstop_eur?euro(c.good_deal_layover_eur*100):'Getrennte Ziele';
  $('budget-note').textContent=c.good_deal_layover_eur===c.good_deal_nonstop_eur?'pro Person · Hin und zurück':`Umstieg ${euro(c.good_deal_layover_eur*100)} / Direkt ${euro(c.good_deal_nonstop_eur*100)}`;
  $('offer-count').textContent=data.offers.length;
  $('trip-dates').textContent=`${day(c.departure_start)} – ${day(c.departure_end)} ${c.departure_start.slice(0,4)}`;
  $('trip-days').textContent=`${c.min_trip_days}–${c.max_trip_days} Tage`;
  $('offer-timestamp').textContent=data.offers_as_of?`${baggageLabels[$('baggage').value]} · ${when(data.offers_as_of)} · Berlin`:'Noch keine geprüften Preise für diese Variante';
}
function deltaText(c) {
  return c.delta===null?'Erste Messung':c.delta===0?'→ Unverändert':`${c.delta<0?'↓':'↑'} ${euro(Math.abs(c.delta))} (${c.percent>0?'+':''}${c.percent.toFixed(1).replace('.',',')} %)`;
}
function renderOffers() {
  const filters=Object.fromEntries(new FormData($('filters'))), offers=filteredOffers(data.offers,filters), body=$('offers-body');
  body.replaceChildren();
  $('results-count').textContent=`${offers.length} von ${data.offers.length} Angeboten · nach Preis sortiert`;
  $('empty').hidden=offers.length>0;
  $('empty').textContent=data.offers.length?'Keine Angebote für diese Auswahl. Probiere einen anderen Filter.':'Noch keine geprüften Angebote im aktuellen Suchfenster. Den Suchstatus findest du oben.';
  const select=$('history-select');select.replaceChildren();
  offers.forEach(q=>{const o=node('option',`${q.origin} · ${q.category==='nonstop'?'Direkt':'Umstieg'} · ${day(q.departure)}–${day(q.return_date)} · ${q.days} Tage`);o.value=q.id;select.append(o);});
  if(!offers.some(q=>q.id===selectedId))selectedId=offers[0]?.id;
  select.value=selectedId||'';select.disabled=!offers.length;
  for(const q of offers) {
    const c=comparison(q,data.histories[q.id]||[],data.config), row=node('tr'); row.dataset.id=q.id;
    if(q.id===selectedId)row.className='selected-row';
    const route=node('td');route.append(node('strong',`${q.origin} → ${data.config.destination}`),node('small',`${category(q)} · ${q.airlines}`));
    const dates=node('td');dates.append(node('strong',`${day(q.departure)} – ${day(q.return_date)}`),node('small',`${q.days} Tage · ${q.departure.slice(0,4)}`));
    const duration=node('td');duration.append(node('strong',`${hours(q.outbound_minutes)} / ${hours(q.inbound_minutes)}`),node('small',`Hin / zurück · Stopps ${q.outbound_stops}/${q.inbound_stops}`));
    const price=node('td');price.append(node('strong',euro(q.price),'price'),node('div',deltaText(c),`delta ${c.delta===null||c.delta===0?'neutral':c.delta<0?'down':'up'}`));
    if($('baggage').value!=='base') {
      price.append(node('small','Gepäck-Endpreis nicht bestätigt','baggage-unconfirmed'));
      price.append(node('small',`${baggageLabels[$('baggage').value]} angefragt · kg: keine Angabe`,'baggage-detail'));
      const base=matchingBase(q,rootData,data);
      if(base) {
        const delta=q.price-base.price;
        price.append(node('small',`Basis gleicher Flüge: ${euro(base.price)} · Differenz ${delta>0?'+':''}${euro(delta)}`,'baggage-detail'));
        price.append(node('small','Preisdifferenz der Suchen, keine bestätigte Einzelgebühr.','baggage-detail'));
      } else price.append(node('small','Kein direkter Basisvergleich: Flüge oder Datenstand abweichend.','baggage-detail'));
    }
    const verdict=node('td'), verdictLabel=$('baggage').value!=='base'?'Tarif prüfen':c.verdict;
    verdict.append(node('span',verdictLabel,`pill ${verdictLabel==='Kauf prüfen'?'good':verdictLabel==='Beobachten'?'watch':'mid'}`));
    const actionCell=node('td'),actions=node('div',undefined,'actions'),button=node('button','Verlauf','chart-button');button.type='button';button.setAttribute('aria-label',`Preisverlauf ${q.origin}, ${q.departure} bis ${q.return_date}, ${q.category==='nonstop'?'Direkt':'Umstieg'}`);
    button.addEventListener('click',()=>{selectedId=q.id;select.value=q.id;renderChart();$('verlauf').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});select.focus({preventScroll:true});});actions.append(button);
    const url=safeFlightLink(q.link);if(url){const link=node('a','Flug suchen ↗','book-link');link.href=url;link.target='_blank';link.rel='noopener noreferrer';link.setAttribute('aria-label',`Flug ${q.origin}, ${q.departure} auf Google Flights suchen`);actions.append(link);}
    actionCell.append(actions);row.append(route,dates,duration,price,verdict,actionCell);body.append(row);
  }
  renderChart();
}
function renderChart() {
  const q=data.offers.find(q=>q.id===selectedId), chart=$('chart');chart.replaceChildren();
  document.querySelectorAll('#offers-body tr').forEach(row=>row.classList.toggle('selected-row',row.dataset.id===selectedId));
  if(!q){$('chart-price').textContent='—';$('chart-change').textContent='Keine Auswahl';chart.append(node('p','Für diesen Filter sind noch keine Preisbeobachtungen verfügbar.','empty'));$('chart-note').textContent='Wähle einen anderen Filter, um vorhandene Verläufe anzuzeigen.';return;}
  const points=(data.histories[q.id]||[]).filter(p=>Date.parse(p.at)<=Date.parse(q.at)).sort((a,b)=>Date.parse(a.at)-Date.parse(b.at)), c=comparison(q,points,data.config);
  $('chart-price').textContent=euro(q.price);$('chart-change').textContent=deltaText(c)+' · zur vorherigen Messung';
  $('chart-note').textContent=`${baggageLabels[$('baggage').value]} · ${points.length} Messungen${points.length?' seit '+when(points[0].at):''}. ${c.low===null?'Noch kein früherer Vergleichspreis.':'Bisheriges Tief vor dieser Messung: '+euro(c.low)+'.'} Gleiche Reisedaten, Flugart und Gepäcksuche, ggf. andere Airline.`;
  if(!points.length){chart.append(node('p','Noch keine Historie verfügbar.','empty'));return;}
  const width=640,height=228,left=48,right=18,top=16,bottom=35;
  const values=points.map(p=>p.price).concat(c.threshold), min=Math.floor((Math.min(...values)-2000)/2500)*2500, max=Math.ceil((Math.max(...values)+2000)/2500)*2500;
  const times=points.map(p=>Date.parse(p.at)), start=times[0],end=times[times.length-1];
  const x=t=>end===start?(left+width-right)/2:left+(t-start)/(end-start)*(width-left-right),y=v=>top+(max-v)/(max-min)*(height-top-bottom);
  const svg=svgNode('svg',{viewBox:`0 0 ${width} ${height}`,role:'img','aria-label':`Preisverlauf ${q.origin}, ${q.departure} bis ${q.return_date}: ${points.length} Messungen, aktuell ${euro(q.price)}`});
  for(let i=0;i<4;i++){const value=min+(max-min)*i/3,pos=y(value);svg.append(svgNode('line',{x1:left,y1:pos,x2:width-right,y2:pos,class:'chart-grid'}),svgNode('text',{x:left-9,y:pos+3,'text-anchor':'end',class:'chart-label'},Math.round(value/100)+' €'));}
  const coords=points.map((p,i)=>[x(times[i]),y(p.price)]);
  if(coords.length>1){svg.append(svgNode('path',{d:`M ${coords[0][0]} ${height-bottom} L `+coords.map(p=>p.join(' ')).join(' L ')+` L ${coords.at(-1)[0]} ${height-bottom} Z`,class:'chart-area'}));}
  svg.append(svgNode('line',{x1:left,x2:width-right,y1:y(c.threshold),y2:y(c.threshold),class:'chart-budget'}));
  if(coords.length>1)svg.append(svgNode('path',{d:'M '+coords.map(p=>p.join(' ')).join(' L '),class:'chart-line'}));
  coords.forEach(([cx,cy],i)=>{const dot=svgNode('circle',{cx,cy,r:4,class:'chart-point',tabindex:0,'aria-label':`${when(points[i].at)}: ${euro(points[i].price)}`});dot.append(svgNode('title',{},`${when(points[i].at)} · ${euro(points[i].price)}`));svg.append(dot);});
  [0,...(times.length>1?[times.length-1]:[])].forEach((i,j)=>svg.append(svgNode('text',{x:x(times[i]),y:height-8,'text-anchor':times.length===1?'middle':j?'end':'start',class:'chart-label'},when(points[i].at))));
  chart.append(svg);
}
async function load() {
  $('reload').disabled=true;
  try {
    const response=await fetch('./data.json',{cache:'no-store'});if(!response.ok)throw new Error('Fetch failed');
    const value=await response.json();if(value.version!==1||!Array.isArray(value.offers)||!value.config||!value.histories)throw new Error('Invalid data');
    rootData=value;fillSelect('origin',value.config.origins,v=>({DUS:'Düsseldorf (DUS)',FRA:'Frankfurt (FRA)',AMS:'Amsterdam (AMS)'})[v]||v);
    fillSelect('days',Array.from({length:value.config.max_trip_days-value.config.min_trip_days+1},(_,i)=>value.config.min_trip_days+i),v=>`${v} Tage`);
    activateBaggage();
  } catch {
    $('status-label').textContent='Datenabruf fehlgeschlagen';$('warning').hidden=false;
    $('warning').textContent='Die Preisdaten konnten nicht geladen werden. Bitte später aktualisieren oder die Suchläufe auf GitHub prüfen. Bereits angezeigte Preise können veraltet sein.';
    if(!data){$('results-count').textContent='Keine Daten geladen';$('empty').hidden=false;$('empty').textContent='Es werden keine Beispielpreise angezeigt.';}
  } finally {$('reload').disabled=false;}
}
$('filters').addEventListener('submit',event=>event.preventDefault());
$('filters').addEventListener('change',()=>{if(data)renderOffers();});
$('filters').addEventListener('reset',()=>{setTimeout(()=>{if(data)renderOffers();},0);});
$('history-select').addEventListener('change',event=>{selectedId=event.target.value;renderChart();});
$('reload').addEventListener('click',load);
$('baggage').addEventListener('change',()=>{if(rootData){selectedId=null;activateBaggage();}});
load();

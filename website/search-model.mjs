// Settings form logic without DOM access. The tracker re-validates everything
// (tracker/config.py, scripts/search_settings.py); these checks give early feedback.

// Settings that start a separate price history (tracker/config.py: Config.scope).
export const scopeFields=['destination','adults','travel_class','max_direction_minutes','hide_separate_tickets','carry_on_bags','checked_bags',
  'max_stops','airlines','airlines_exclude'];
export const priceFields=['good_deal_nonstop_eur','good_deal_layover_eur','drop_eur','realert_improvement_eur'];
const MAX_AIRLINES=25;

export const fold=text=>String(text).normalize('NFD').replace(/[\u0300-\u036f]/g,'').replace(/ß/g,'ss').toLowerCase();
const words=text=>fold(text).split(/[^a-z0-9]+/).filter(Boolean);

export function airportInfo(table, code) {
  const entry=table.airports[code];
  return entry?{code,city:entry[0],country:table.countries[entry[1]]||'',name:entry[2],alias:table.aliases?.[code]||''}:null;
}

// How well a query matches, best first: exact code, place name start, code start,
// any word of the place, airport name or German alias, then anywhere in the name.
function textRank(q, code, names, other) {
  if(code.toLowerCase()===q)return 0;
  if(names.some(name=>fold(name).startsWith(q)))return 1;
  if(code.toLowerCase().startsWith(q))return 2;
  if(names.some(name=>words(name).some(word=>word.startsWith(q))))return 3;
  if(other.some(name=>words(name).some(word=>word.startsWith(q))))return 4;
  if([...names,...other].some(name=>fold(name).includes(q)))return 5;
  return -1;
}

// Within the same match quality, large airports come first ("bang" → Bangkok before Bangalore).
export function searchAirports(table, query, limit=8) {
  const q=fold(query).trim();
  if(!q)return [];
  const major=new Map((table.major||[]).map((code,i)=>[code,i]));
  const ranked=[];
  for(const code of Object.keys(table.airports)) {
    const info=airportInfo(table,code);
    const rank=textRank(q,code,[info.city,info.alias].filter(Boolean),[info.name,info.country]);
    if(rank>=0)ranked.push({rank,size:major.get(code)??Infinity,info});
  }
  return ranked.sort((a,b)=>a.rank-b.rank || a.size-b.size || a.info.city.localeCompare(b.info.city,'en') || a.info.code.localeCompare(b.info.code))
    .slice(0,limit).map(x=>x.info);
}

export function searchAirlines(names, query, limit=8) {
  const q=fold(query).trim();
  if(!q)return [];
  const ranked=[];
  for(const [code,name] of Object.entries(names)) {
    const rank=textRank(q,code,[name],[]);
    if(rank>=0)ranked.push({rank,code,name});
  }
  return ranked.sort((a,b)=>a.rank-b.rank || a.name.localeCompare(b.name,'en')).slice(0,limit).map(({code,name})=>({code,name}));
}

const dayMs=86400000;
const isoDate=value=>/^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(value+'T00:00:00Z'));
const addDays=(value,days)=>new Date(Date.parse(value+'T00:00:00Z')+days*dayMs).toISOString().slice(0,10);

// Mirrors tracker/planner.py: request_estimate.
export function requestEstimate(config, today) {
  const start=[config.departure_start,addDays(today,1)].sort().at(-1);
  const count=(from,to)=>Math.max(0,Math.round((Date.parse(to)-Date.parse(from))/dayMs)+1);
  const profiles=config.max_stops===0?1:2;
  // Every departure day with every trip length; with a latest return date, longer
  // trips have fewer departure days.
  let pairs=0;
  for(let length=config.min_trip_days;length<=config.max_trip_days;length++) {
    const last=config.latest_return?[config.departure_end,addDays(config.latest_return,-length)].sort()[0]:config.departure_end;
    pairs+=count(start,last);
  }
  const days=config.latest_return?count(start,[config.departure_end,addDays(config.latest_return,-config.min_trip_days)].sort()[0]):count(start,config.departure_end);
  const calendar=pairs*config.origins.length*profiles;
  const verification=config.max_verifications_per_run*(1+config.outbound_candidates);
  const requests=calendar+verification;
  const seconds=Math.round(requests*Math.max(config.request_interval_seconds,1.5/config.max_parallel_requests));
  return {days,calendar,verification,requests,seconds};
}

// Same rule as Python's str.isprintable(): no control, format or separator characters except a space.
const printable=name=>!/[\p{C}\p{Z}]/u.test(name.replace(/ /g,''));

// Field errors of one trip, and its request estimate once every field is valid.
export function tripErrors(config, meta, table, today) {
  const errors={}, error=(field,text)=>{errors[field]??=text;};
  const {int:ints,float:floats,display_name:maxName}=meta.limits;
  if(!config.origins.length)error('origins','Choose at least one departure airport.');
  for(const code of config.origins)if(!table.airports[code])error('origins',`${code} is not supported by the flight search.`);
  if(!table.airports[config.destination])error('destination','Choose a supported destination.');
  else if(config.origins.includes(config.destination))error('destination','The destination cannot be a departure airport.');
  for(const field of ['departure_start','departure_end'])if(!isoDate(config[field]))error(field,'Enter a valid date.');
  if(!errors.departure_start && !errors.departure_end) {
    const span=(Date.parse(config.departure_end)-Date.parse(config.departure_start))/dayMs;
    if(span<0)error('departure_end','Cannot be before the earliest departure.');
    else if(span>365)error('departure_end','The departure window can cover at most 366 days.');
    else if(config.departure_end<=today)error('departure_end','Must be after today, otherwise there is nothing to search.');
  }
  for(const [field,[low,high]] of Object.entries(ints)) {
    const value=config[field];
    if(!Number.isInteger(value) || value<low || value>high)error(field,`Whole number from ${low} to ${high}.`);
  }
  if(!errors.min_trip_days && !errors.max_trip_days && config.min_trip_days>config.max_trip_days)
    error('max_trip_days','Cannot be shorter than the shortest trip.');
  if(config.latest_return!==null && config.latest_return!==undefined) {
    if(!isoDate(config.latest_return))error('latest_return','Enter a valid date.');
    else if(!errors.departure_start && !errors.min_trip_days && config.latest_return<addDays(config.departure_start,config.min_trip_days))
      error('latest_return','Too early: even the shortest trip from the earliest departure returns later.');
  }
  for(const [field,[low,high]] of Object.entries(floats)) {
    const value=config[field];
    if(!Number.isFinite(value) || value<low || value>high)error(field,`Number from ${low} to ${high}.`);
  }
  for(const field of priceFields) {
    const value=config[field];
    if(!Number.isFinite(value) || value<0.01 || value>10000000)error(field,'Enter an amount above €0.');
  }
  if(!Object.hasOwn(meta.travel_classes,config.travel_class))error('travel_class','Choose a cabin.');
  if(![null,0,1,2].includes(config.max_stops))error('max_stops','Choose a number of stops.');
  for(const field of ['airlines','airlines_exclude']) {
    const codes=config[field];
    if(codes.length>MAX_AIRLINES)error(field,`At most ${MAX_AIRLINES} airlines.`);
    for(const code of codes)if(!Object.hasOwn(meta.airlines,code))error(field,`${code} is not supported by the flight search.`);
  }
  const both=config.airlines.filter(code=>config.airlines_exclude.includes(code));
  if(both.length)error('airlines_exclude',`${both.join(', ')} cannot be both included and excluded.`);
  for(const [code,targets] of Object.entries(config.origin_targets||{})) {
    if(!config.origins.includes(code))error('origin_targets',`${code} is not a departure airport of this trip.`);
    for(const value of Object.values(targets))
      if(!Number.isFinite(value) || value<0.01 || value>10000000)error('origin_targets',`Price target for ${code}: enter an amount above €0.`);
  }
  for(const [code,name] of Object.entries(config.display_names))
    if(!name || name.length>maxName || name!==name.trim() || !printable(name))error('display_names',`Name for ${code}: 1–${maxName} characters, no control characters.`);
  return {errors,estimate:Object.keys(errors).length?null:requestEstimate(config,today)};
}

// All trips of a run share one request and time budget.
export function budgetCheck(estimate, config, trips=1) {
  const together=trips>1?` for all ${trips} trips together`:'';
  if(estimate.requests>config.max_http_attempts_per_run)
    return {error:`Too many requests: about ${estimate.requests} per run${together} with a budget of ${config.max_http_attempts_per_run}. Choose fewer departure days, trip lengths or airports${trips>1?', or fewer trips':''}.`};
  if(estimate.seconds>config.max_run_seconds)
    return {error:`Search too long: about ${Math.floor(estimate.seconds/60)} minutes per run${together} with a limit of ${Math.floor(config.max_run_seconds/60)} minutes.`};
  if(estimate.requests>0.8*config.max_http_attempts_per_run || estimate.seconds>0.8*config.max_run_seconds)
    return {warning:'The search almost uses up its budget. Repeated requests during disruptions could end a run early.'};
  return {};
}

// All trips: field errors per trip, shared settings once, and one estimate for the run.
export function validateTrips(configs, meta, table, today) {
  const shared=new Set(meta.shared_fields), results=configs.map(config=>tripErrors(config,meta,table,today));
  const pick=(errors,keep)=>Object.fromEntries(Object.entries(errors).filter(([key])=>shared.has(key)===keep));
  const errors=pick(results[0].errors,true), warnings=[];
  let estimate=null;
  if(results.every(result=>result.estimate)) {
    const sum=key=>results.reduce((total,result)=>total+result.estimate[key],0);
    estimate={calendar:sum('calendar'),verification:sum('verification'),requests:sum('requests'),seconds:sum('seconds'),
      trips:results.map(result=>result.estimate)};
    const check=budgetCheck(estimate,configs[0],configs.length);
    if(check.error)errors.estimate=check.error;
    if(check.warning)warnings.push(check.warning);
  }
  return {errors,trips:results.map(result=>pick(result.errors,false)),warnings,estimate};
}

const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
export function changedFields(current, next) {
  return Object.keys(next).filter(key=>!same(current[key],next[key]));
}

export function shownValue(key, value, meta) {
  if(key==='latest_return')return value||'none';
  if(key==='origins')return value.join(', ');
  if(key==='airlines')return value.join(', ')||'all';
  if(key==='airlines_exclude')return value.join(', ')||'none';
  if(key==='max_stops')return value===null?'any':value===0?'non-stop only':`up to ${value}`;
  if(key==='display_names')return Object.entries(value).sort().map(([k,v])=>`${k}: ${v}`).join(', ')||'automatic';
  if(key==='origin_targets')return Object.entries(value).sort().map(([code,targets])=>`${code}: `+
    ['nonstop','layover'].filter(k=>k in targets).map(k=>`${k==='nonstop'?'non-stop':'with stops'} €${targets[k]}`).join(', ')).join('; ')||'same for all airports';
  if(key==='travel_class')return meta.travel_classes[value]||value;
  if(typeof value==='boolean')return value?'yes':'no';
  return String(value);
}

// New trips get an id from their destination ("ams", then "ams-2"); saved trips keep
// theirs, because the id keeps a trip's price history and its ?trip= link.
export function tripIds(trips) {
  const taken=new Set(trips.map(trip=>trip.id).filter(Boolean));
  return trips.map(trip=>{
    if(trip.id)return trip.id;
    const base=String(trip.config.destination||'').toLowerCase().replace(/[^a-z0-9]/g,'')||'trip';
    let id=base;
    for(let n=2;taken.has(id);n++)id=`${base}-${n}`;
    taken.add(id);return id;
  });
}

export function tripName(config, configs) {
  if(!config.destination)return 'New trip';
  const twins=configs.filter(other=>other.destination===config.destination);
  return twins.length>1?`${config.destination} (${twins.indexOf(config)+1})`:config.destination;
}

export function tripSummary(config) {
  return `${config.origins.join(', ')} → ${config.destination}, ${config.departure_start} to ${config.departure_end}, ${config.min_trip_days}–${config.max_trip_days} days${config.latest_return?`, back by ${config.latest_return}`:''}`;
}

// Optional settings at these values are left out of the issue (as in config.json).
const optional={latest_return:null,max_stops:null,airlines:[],airlines_exclude:[],origin_targets:{},display_names:{}};

// The issue's settings block: every trip in config.json field order, shared request settings once.
export function settingsJson(meta, trips, primary) {
  const ids=tripIds(trips), shared=meta.shared_fields;
  const order=Object.keys(meta.trips[0]).filter(key=>key!=='id' && !shared.includes(key));
  const fields=config=>order.filter(key=>!(Object.hasOwn(optional,key) && same(config[key],optional[key]))).map(key=>[key,config[key]]);
  return {primary_trip:ids[primary],trips:trips.map((trip,i)=>({id:ids[i],...Object.fromEntries(fields(trip.config))})),
    ...Object.fromEntries(shared.map(key=>[key,trips[0].config[key]]))};
}

// Rows of "what changes", matched by trip id like the reply on the issue.
export function settingsChanges(meta, trips, primary) {
  const ids=tripIds(trips), configs=trips.map(trip=>trip.config), shared=meta.shared_fields;
  const row=(label,key,before,after)=>({label,before:shownValue(key,before,meta),after:shownValue(key,after,meta)});
  const fieldRows=(old,next,prefix,skip)=>changedFields(old,next).filter(key=>key!=='id' && !skip.includes(key))
    .map(key=>row(prefix+(meta.labels[key]||key),key,old[key],next[key]));
  if(meta.trips.length===1 && trips.length===1 && ids[0]===meta.trips[0].id)return fieldRows(meta.trips[0],configs[0],'',[]);
  const before=new Map(meta.trips.map(config=>[config.id,config]));
  const rows=shared.filter(key=>!same(meta.trips[0][key],configs[0][key])).map(key=>row(meta.labels[key]||key,key,meta.trips[0][key],configs[0][key]));
  if(ids[primary]!==meta.primary_trip) {
    const old=before.get(meta.primary_trip);
    rows.push({label:meta.labels.primary_trip,before:tripName(old,meta.trips),after:tripName(configs[primary],configs)});
  }
  configs.forEach((config,i)=>{
    const old=before.get(ids[i]);
    if(old)rows.push(...fieldRows(old,config,tripName(config,configs)+' · ',shared));
    else rows.push({label:meta.labels.trips,before:'—',after:'added: '+tripSummary(config)});
  });
  for(const old of meta.trips)if(!ids.includes(old.id))rows.push({label:meta.labels.trips,before:tripSummary(old),after:'removed'});
  return rows;
}

export function issueTitle(settings) {
  const trips=settings.trips;
  return trips.length===1?`Change search: ${trips[0].origins.join(', ')} → ${trips[0].destination}`
    :`Change search: ${trips.length} trips (${trips.map(trip=>trip.destination).join(', ')})`;
}

const LISTED_CHANGES=25;
export function issueBody(meta, settings, changes) {
  const several=settings.trips.length>1;
  const trips=settings.trips.map(t=>`- ${t.origins.join(', ')} → ${t.destination} · departures ${t.departure_start} to ${t.departure_end} · ${t.min_trip_days}–${t.max_trip_days} days${t.latest_return?` · back by ${t.latest_return}`:''}${several && t.id===settings.primary_trip?' · shown first':''}`);
  const listed=changes.slice(0,LISTED_CHANGES).map(change=>`- ${change.label}: ${change.before} → ${change.after}`);
  if(changes.length>LISTED_CHANGES)listed.push(`- … and ${changes.length-LISTED_CHANGES} more`);
  return [meta.marker, several?`**New search, ${settings.trips.length} trips:**`:'**New search:**', ...trips, '', '**Changes:**', ...listed, '',
    'Create this issue to apply the search. A workflow checks the settings, replies here and closes the issue. Only issues from the repository owner are applied.',
    '', '```json', JSON.stringify(settings), '```'].join('\n');
}

// GitHub rejects very long addresses; beyond this the body is copied by hand.
export const MAX_URL=8000;
export function issueUrl(meta, settings, changes, withBody=true) {
  const params=new URLSearchParams({title:issueTitle(settings)});
  if(withBody)params.set('body',issueBody(meta,settings,changes));
  return `https://github.com/${meta.repository}/issues/new?${params}`;
}

// Price suggestions. Before a trip has prices, a rough estimate from the flight
// distance; once it has checked prices, the price that a quarter of them reached.
const EARTH_KM=6371;
export function distanceKm(table, from, to) {
  const a=table.airports[from], b=table.airports[to];
  if(!a || !b || typeof a[3]!=='number' || typeof b[3]!=='number')return null;
  const rad=x=>x*Math.PI/180, [lat1,lon1,lat2,lon2]=[a[3],a[4],b[3],b[4]].map(rad);
  const h=Math.sin((lat2-lat1)/2)**2+Math.cos(lat1)*Math.cos(lat2)*Math.sin((lon2-lon1)/2)**2;
  return Math.round(2*EARTH_KM*Math.asin(Math.sqrt(h)));
}
// Round to amounts people would choose: €5 steps below €100, €10 below €1,000, then €50.
export function niceEuro(value) {
  const step=value<100?5:value<1000?10:50;
  return Math.max(step,Math.round(value/step)*step);
}
const CABIN_FACTOR={economy:1,premium_economy:1.6,business:3.2,first_class:5};
// A good round-trip economy price from Central Europe, by one-way distance: about €90
// for Frankfurt–Amsterdam, €490 for New York, €650 for Bangkok, €1,100 for Sydney.
export function estimatedTarget(km) {
  return km<=3000?60+0.08*km:300+0.059*(km-3000);
}
// Strong-deal and re-alert amounts follow the price target (€650 → €50 and €25).
export function alertAmounts(target) {
  return {realert:Math.max(5,niceEuro(target*0.04)),drop:Math.max(10,niceEuro(target*0.08))};
}
function percentile(values, p) {
  const sorted=[...values].sort((a,b)=>a-b), index=(sorted.length-1)*p, low=Math.floor(index);
  return sorted[low]+(sorted[Math.ceil(index)]-sorted[low])*(index-low);
}
export const MIN_PRICES=8;
// Checked prices (cents) of a trip's current offers within its comparison period,
// by category and by departure airport.
export function observedPrices(trip, now=Date.now()) {
  const since=now-trip.config.history_window_days*86400000, prices={nonstop:[],layover:[],byOrigin:{}};
  for(const offer of trip.offers||[]) {
    if(!prices[offer.category])continue;
    const own=(prices.byOrigin[offer.origin]??={nonstop:[],layover:[]});
    for(const point of trip.histories?.[offer.id]||[]) {
      const at=Date.parse(point.at);
      if(at>=since && at<=now && Number.isInteger(point.price)){prices[offer.category].push(point.price);own[offer.category].push(point.price);}
    }
  }
  return prices;
}
const median=values=>percentile(values,0.5);
// Suggested price targets. With enough checked prices: per airport and category the
// price a quarter of them reached; the trip's targets are the middle of those, and an
// airport keeps its own target only where it differs by more than 5%. Without prices,
// a rough guide from the distance that knows nothing about differences between airports.
export function suggestPrices(config, table, observed=null) {
  const distances=config.origins.map(origin=>distanceKm(table,origin,config.destination)).filter(km=>km!==null);
  let estimate=null;
  if(config.destination && distances.length) {
    const km=Math.round(distances.reduce((a,b)=>a+b,0)/distances.length);
    const layover=estimatedTarget(km)*(CABIN_FACTOR[config.travel_class]||1)+(config.checked_bags && km<3000?40:0);
    // Long-haul non-stop flights usually cost more than connections.
    estimate={km,layover,nonstop:layover*(km>3000?1.15:1)};
  }
  const airports={};
  for(const origin of config.origins)for(const category of ['nonstop','layover']) {
    const prices=observed?.byOrigin?.[origin]?.[category]||[];
    if(prices.length>=MIN_PRICES)(airports[origin]??={})[category]=niceEuro(percentile(prices,0.25)/100);
  }
  const counts={nonstop:observed?.nonstop?.length||0,layover:observed?.layover?.length||0};
  const finish=(source,nonstop,layover,own)=>{
    const amounts=alertAmounts(Math.min(nonstop,layover));
    return {source,km:estimate?.km??null,counts,airports,values:{good_deal_nonstop_eur:nonstop,good_deal_layover_eur:layover,
      realert_improvement_eur:amounts.realert,drop_eur:amounts.drop,...(own?{origin_targets:own}:{})}};
  };
  if(!Object.keys(airports).length)
    return estimate?finish('estimate',niceEuro(estimate.nonstop),niceEuro(estimate.layover),null):null;
  const middle=category=>{const values=Object.values(airports).map(t=>t[category]).filter(v=>v!==undefined);return values.length?niceEuro(median(values)):null;};
  const ratio=estimate?estimate.nonstop/estimate.layover:1;
  let nonstop=middle('nonstop'), layover=middle('layover');
  layover??=niceEuro(nonstop/ratio);nonstop??=niceEuro(layover*ratio);
  const own={};
  for(const origin of config.origins)for(const category of ['nonstop','layover']) {
    const value=airports[origin]?.[category], trip=category==='nonstop'?nonstop:layover;
    if(value!==undefined && Math.abs(value-trip)>trip*0.05)(own[origin]??={})[category]=value;
  }
  return finish('prices',nonstop,layover,own);
}

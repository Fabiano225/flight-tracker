// Settings form logic without DOM access. The tracker re-validates everything
// (tracker/config.py, scripts/search_settings.py); these checks give early feedback.

// Settings that start a separate price history (tracker/config.py: Config.scope).
export const scopeFields=['destination','travel_class','max_direction_minutes','hide_separate_tickets','carry_on_bags','checked_bags',
  'max_stops','airlines','airlines_exclude'];
export const priceFields=['good_deal_nonstop_eur','good_deal_layover_eur','drop_eur','realert_improvement_eur'];
export const listFields=['origins','airlines','airlines_exclude'];
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
  const days=Math.max(0,Math.round((Date.parse(config.departure_end)-Date.parse(start))/dayMs)+1);
  const durations=Math.max(0,config.max_trip_days-config.min_trip_days+1);
  const profiles=config.max_stops===0?1:2;
  const calendar=days*durations*config.origins.length*profiles;
  const verification=config.max_verifications_per_run*(1+config.outbound_candidates);
  const requests=calendar+verification;
  const seconds=Math.round(requests*Math.max(config.request_interval_seconds,1.5/config.max_parallel_requests));
  return {days,calendar,verification,requests,seconds};
}

// Same rule as Python's str.isprintable(): no control, format or separator characters except a space.
const printable=name=>!/[\p{C}\p{Z}]/u.test(name.replace(/ /g,''));

export function validate(config, meta, table, today) {
  const errors={}, warnings=[], error=(field,text)=>{errors[field]??=text;};
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
  for(const [code,name] of Object.entries(config.display_names))
    if(!name || name.length>maxName || name!==name.trim() || !printable(name))error('display_names',`Name for ${code}: 1–${maxName} characters, no control characters.`);
  let estimate=null;
  if(!Object.keys(errors).length) {
    estimate=requestEstimate(config,today);
    if(estimate.requests>config.max_http_attempts_per_run)
      error('estimate',`Too many requests: about ${estimate.requests} per run with a budget of ${config.max_http_attempts_per_run}. Choose fewer departure days, trip lengths or airports.`);
    else if(estimate.seconds>config.max_run_seconds)
      error('estimate',`Search too long: about ${Math.floor(estimate.seconds/60)} minutes per run with a limit of ${Math.floor(config.max_run_seconds/60)} minutes.`);
    else if(estimate.requests>0.8*config.max_http_attempts_per_run || estimate.seconds>0.8*config.max_run_seconds)
      warnings.push('The search almost uses up its budget. Repeated requests during disruptions could end a run early.');
  }
  return {errors,warnings,estimate};
}

const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
export function changedFields(current, next) {
  return Object.keys(next).filter(key=>!same(current[key],next[key]));
}

export function shownValue(key, value, meta) {
  if(key==='origins')return value.join(', ');
  if(key==='airlines')return value.join(', ')||'all';
  if(key==='airlines_exclude')return value.join(', ')||'none';
  if(key==='max_stops')return value===null?'any':value===0?'non-stop only':`up to ${value}`;
  if(key==='display_names')return Object.entries(value).sort().map(([k,v])=>`${k}: ${v}`).join(', ')||'automatic';
  if(key==='travel_class')return meta.travel_classes[value]||value;
  if(typeof value==='boolean')return value?'yes':'no';
  return String(value);
}

// Keep the field order of config.json so the stored file stays readable.
export function orderedConfig(meta, config) {
  return Object.fromEntries(Object.keys(meta.config).map(key=>[key,config[key]]));
}

export function issueTitle(config) {
  return `Change search: ${config.origins.join(', ')} → ${config.destination}`;
}

export function issueBody(meta, config) {
  const changes=changedFields(meta.config,config).map(key=>
    `- ${meta.labels[key]||key}: ${shownValue(key,meta.config[key],meta)} → ${shownValue(key,config[key],meta)}`);
  return [meta.marker,
    `**New search:** ${config.origins.join(', ')} → ${config.destination} · departures ${config.departure_start} to ${config.departure_end} · ${config.min_trip_days}–${config.max_trip_days} days`,
    '', '**Changes:**', ...changes, '',
    'Create this issue to apply the search. A workflow checks the settings, replies here and closes the issue. Only issues from the repository owner are applied.',
    '', '```json', JSON.stringify(orderedConfig(meta,config),null,2), '```'].join('\n');
}

export function issueUrl(meta, config) {
  const params=new URLSearchParams({title:issueTitle(config),body:issueBody(meta,config)});
  return `https://github.com/${meta.repository}/issues/new?${params}`;
}

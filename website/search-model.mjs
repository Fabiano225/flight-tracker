// Settings form logic without DOM access. The tracker re-validates everything
// (tracker/config.py, scripts/search_settings.py); these checks give early feedback.

// Settings that start a separate price history (tracker/config.py: Config.scope).
export const scopeFields=['destination','travel_class','max_direction_minutes','hide_separate_tickets','carry_on_bags','checked_bags'];
export const priceFields=['good_deal_nonstop_eur','good_deal_layover_eur','drop_eur','realert_improvement_eur'];

export const fold=text=>String(text).normalize('NFD').replace(/[̀-ͯ]/g,'').replace(/ß/g,'ss').toLowerCase();

export function airportInfo(table, code) {
  const entry=table.airports[code];
  return entry?{code,city:entry[0],country:table.countries[entry[1]]||'',name:entry[2]}:null;
}

// Best matches first: exact code, city start, code start, then any word of city or airport name.
export function searchAirports(table, query, limit=8) {
  const q=fold(query).trim();
  if(!q)return [];
  const ranked=[];
  for(const code of Object.keys(table.airports)) {
    const info=airportInfo(table,code), city=fold(info.city), name=fold(info.name), lower=code.toLowerCase();
    const rank=lower===q?0:city.startsWith(q)?1:lower.startsWith(q)?2
      :city.includes(q)?3:name.split(/[^a-z0-9]+/).some(word=>word.startsWith(q))?4:name.includes(q)?5:-1;
    if(rank>=0)ranked.push({rank,info});
  }
  return ranked.sort((a,b)=>a.rank-b.rank || a.info.city.localeCompare(b.info.city,'de') || a.info.code.localeCompare(b.info.code))
    .slice(0,limit).map(x=>x.info);
}

const dayMs=86400000;
const isoDate=value=>/^\d{4}-\d{2}-\d{2}$/.test(value) && !Number.isNaN(Date.parse(value+'T00:00:00Z'));
const addDays=(value,days)=>new Date(Date.parse(value+'T00:00:00Z')+days*dayMs).toISOString().slice(0,10);

// Mirrors tracker/planner.py: request_estimate.
export function requestEstimate(config, today) {
  const start=[config.departure_start,addDays(today,1)].sort().at(-1);
  const days=Math.max(0,Math.round((Date.parse(config.departure_end)-Date.parse(start))/dayMs)+1);
  const durations=Math.max(0,config.max_trip_days-config.min_trip_days+1);
  const calendar=days*durations*config.origins.length*2;
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
  if(!config.origins.length)error('origins','Mindestens einen Abflughafen wählen.');
  for(const code of config.origins)if(!table.airports[code])error('origins',`${code} wird von der Flugsuche nicht unterstützt.`);
  if(!table.airports[config.destination])error('destination','Ein unterstütztes Ziel wählen.');
  else if(config.origins.includes(config.destination))error('destination','Das Ziel darf kein Abflughafen sein.');
  for(const field of ['departure_start','departure_end'])if(!isoDate(config[field]))error(field,'Gültiges Datum eingeben.');
  if(!errors.departure_start && !errors.departure_end) {
    const span=(Date.parse(config.departure_end)-Date.parse(config.departure_start))/dayMs;
    if(span<0)error('departure_end','Darf nicht vor dem frühesten Abflug liegen.');
    else if(span>365)error('departure_end','Das Abflugfenster darf höchstens 366 Tage umfassen.');
    else if(config.departure_end<=today)error('departure_end','Muss nach heute liegen, sonst gibt es nichts zu suchen.');
  }
  for(const [field,[low,high]] of Object.entries(ints)) {
    const value=config[field];
    if(!Number.isInteger(value) || value<low || value>high)error(field,`Ganze Zahl von ${low} bis ${high}.`);
  }
  if(!errors.min_trip_days && !errors.max_trip_days && config.min_trip_days>config.max_trip_days)
    error('max_trip_days','Darf nicht kürzer als die Mindestdauer sein.');
  for(const [field,[low,high]] of Object.entries(floats)) {
    const value=config[field];
    if(!Number.isFinite(value) || value<low || value>high)error(field,`Zahl von ${String(low).replace('.',',')} bis ${high}.`);
  }
  for(const field of priceFields) {
    const value=config[field];
    if(!Number.isFinite(value) || value<0.01 || value>10000000)error(field,'Betrag über 0 € eingeben.');
  }
  if(!Object.hasOwn(meta.travel_classes,config.travel_class))error('travel_class','Reiseklasse wählen.');
  for(const [code,name] of Object.entries(config.display_names))
    if(!name || name.length>maxName || name!==name.trim() || !printable(name))error('display_names',`Name für ${code}: 1–${maxName} Zeichen, ohne Steuerzeichen.`);
  let estimate=null;
  if(!Object.keys(errors).length) {
    estimate=requestEstimate(config,today);
    if(estimate.requests>config.max_http_attempts_per_run)
      error('estimate',`Zu viele Anfragen: etwa ${estimate.requests} pro Suchlauf bei einem Budget von ${config.max_http_attempts_per_run}. Weniger Abflugtage, Reisedauern oder Flughäfen wählen.`);
    else if(estimate.seconds>config.max_run_seconds)
      error('estimate',`Zu lange Suche: etwa ${Math.floor(estimate.seconds/60)} Minuten pro Suchlauf bei einem Limit von ${Math.floor(config.max_run_seconds/60)} Minuten.`);
    else if(estimate.requests>0.8*config.max_http_attempts_per_run || estimate.seconds>0.8*config.max_run_seconds)
      warnings.push('Die Suche schöpft ihr Budget fast aus. Wiederholte Anfragen bei Störungen könnten einen Suchlauf vorzeitig beenden.');
  }
  return {errors,warnings,estimate};
}

const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
export function changedFields(current, next) {
  return Object.keys(next).filter(key=>!same(current[key],next[key]));
}

export function shownValue(key, value, meta) {
  if(key==='origins')return value.join(', ');
  if(key==='display_names')return Object.entries(value).sort().map(([k,v])=>`${k}: ${v}`).join(', ')||'automatisch';
  if(key==='travel_class')return meta.travel_classes[value]||value;
  if(typeof value==='boolean')return value?'ja':'nein';
  return String(value);
}

// Keep the field order of config.json so the stored file stays readable.
export function orderedConfig(meta, config) {
  return Object.fromEntries(Object.keys(meta.config).map(key=>[key,config[key]]));
}

export function issueTitle(config) {
  return `Suche ändern: ${config.origins.join(', ')} → ${config.destination}`;
}

export function issueBody(meta, config) {
  const changes=changedFields(meta.config,config).map(key=>
    `- ${meta.labels[key]||key}: ${shownValue(key,meta.config[key],meta)} → ${shownValue(key,config[key],meta)}`);
  return [meta.marker,
    `**Neue Suche:** ${config.origins.join(', ')} → ${config.destination} · Abflug ${config.departure_start} bis ${config.departure_end} · ${config.min_trip_days}–${config.max_trip_days} Tage`,
    '', '**Änderungen:**', ...changes, '',
    'Schicke dieses Issue ab, um die Suche zu übernehmen. Ein Workflow prüft die Einstellungen, antwortet hier und schließt das Issue. Übernommen werden nur Issues des Repository-Inhabers.',
    '', '```json', JSON.stringify(orderedConfig(meta,config),null,2), '```'].join('\n');
}

export function issueUrl(meta, config) {
  const params=new URLSearchParams({title:issueTitle(config),body:issueBody(meta,config)});
  return `https://github.com/${meta.repository}/issues/new?${params}`;
}

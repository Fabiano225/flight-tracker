import {airportInfo, searchAirports, validate, changedFields, shownValue, orderedConfig, issueUrl, scopeFields, priceFields} from './search-model.mjs';

const $ = id => document.getElementById(id);
const form = $('settings');
const field = name => form.elements.namedItem(name);
const node = (tag, text, cls) => {const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n;};
const berlinToday = () => new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Berlin'}).format(new Date());
const germanDate = value => value.split('-').reverse().join('.');
const numberValue = value => String(value).trim()==='' ? NaN : Number(value);
let meta, table, state, numberFields;

function cityOf(code) {return airportInfo(table,code)?.city || code;}
function routeCodes() {return [...new Set([...state.origins,state.destination])];}

function fill(config) {
  state={origins:[...config.origins],destination:config.destination,names:{...config.display_names}};
  for(const name of [...numberFields,'departure_start','departure_end','travel_class'])field(name).value=config[name];
  field('duration_hours').value=Math.floor(config.max_direction_minutes/60);
  field('duration_minutes').value=config.max_direction_minutes%60;
  field('hide_separate_tickets').checked=config.hide_separate_tickets;
  field('carry_on_bags').checked=config.carry_on_bags===1;
  field('checked_bags').checked=config.checked_bags===1;
  renderAirports();renderNames();update();
}

function collect() {
  const config={...meta.config,origins:[...state.origins],destination:state.destination};
  for(const name of ['departure_start','departure_end','travel_class'])config[name]=field(name).value;
  for(const name of numberFields)config[name]=numberValue(field(name).value);
  const hours=numberValue(field('duration_hours').value), minutes=numberValue(field('duration_minutes').value);
  config.max_direction_minutes=Number.isInteger(hours) && Number.isInteger(minutes) && hours>=0 && minutes>=0 && minutes<60 ? hours*60+minutes : NaN;
  config.hide_separate_tickets=field('hide_separate_tickets').checked;
  config.carry_on_bags=field('carry_on_bags').checked?1:0;
  config.checked_bags=field('checked_bags').checked?1:0;
  config.display_names=Object.fromEntries(routeCodes().map(code=>[code,(state.names[code]||'').trim()])
    .filter(([code,name])=>name && name!==cityOf(code)));
  return orderedConfig(meta,config);
}

function renderAirports() {
  const chip=(code,removable)=>{
    const item=node('li',undefined,'chip'), info=airportInfo(table,code);
    item.append(node('strong',code),node('span',info?`${info.city}${info.country?' · '+info.country:''}`:'unbekannt'));
    if(removable){
      const remove=node('button','✕','chip-remove');remove.type='button';
      remove.setAttribute('aria-label',`${info?.city||code} (${code}) entfernen`);
      remove.addEventListener('click',()=>{state.origins=state.origins.filter(c=>c!==code);renderAirports();renderNames();update();$('origins-input').focus();});
      item.append(remove);
    }
    return item;
  };
  $('origins-chips').replaceChildren(...state.origins.map(code=>chip(code,true)));
  $('destination-chips').replaceChildren(chip(state.destination,false));
}

function renderNames() {
  const box=$('display-names');box.replaceChildren();
  for(const code of routeCodes()) {
    const label=node('label',`Name für ${code}`,'field'), input=node('input');
    input.type='text';input.maxLength=meta.limits.display_name;input.value=state.names[code]||'';
    input.placeholder=`automatisch: ${cityOf(code)}`;input.dataset.code=code;
    input.setAttribute('aria-describedby','display_names-error');
    input.addEventListener('input',()=>{state.names[code]=input.value;});
    label.append(input);box.append(label);
  }
}

function combo(kind) {
  const input=$(`${kind}-input`), list=$(`${kind}-list`);
  let options=[], active=-1;
  const close=()=>{list.hidden=true;input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');active=-1;};
  const highlight=()=>{
    [...list.children].forEach((item,i)=>{item.setAttribute('aria-selected',String(i===active));item.classList.toggle('active',i===active);});
    if(active>=0){input.setAttribute('aria-activedescendant',`${kind}-option-${active}`);list.children[active].scrollIntoView({block:'nearest'});}
  };
  const choose=info=>{
    if(kind==='origins'){if(!state.origins.includes(info.code))state.origins.push(info.code);}
    else state.destination=info.code;
    input.value='';close();renderAirports();renderNames();update();input.focus();
  };
  const show=()=>{
    const query=input.value.trim();
    options=searchAirports(table,query,30).filter(info=>kind==='origins'?!state.origins.includes(info.code):info.code!==state.destination).slice(0,8);
    list.replaceChildren();
    options.forEach((info,i)=>{
      const item=node('li');item.id=`${kind}-option-${i}`;item.setAttribute('role','option');item.setAttribute('aria-selected','false');
      item.append(node('strong',`${info.city} (${info.code})`),node('small',[info.name,info.country].filter(Boolean).join(' · ')));
      item.addEventListener('mousedown',event=>event.preventDefault());
      item.addEventListener('click',()=>choose(info));
      list.append(item);
    });
    if(query && !options.length){const empty=node('li','Kein unterstützter Flughafen gefunden.','suggestion-empty');empty.setAttribute('aria-disabled','true');list.append(empty);}
    list.hidden=!query;input.setAttribute('aria-expanded',String(!list.hidden));
  };
  input.addEventListener('input',()=>{active=-1;show();});
  input.addEventListener('focus',()=>{if(input.value.trim())show();});
  input.addEventListener('blur',close);
  input.addEventListener('keydown',event=>{
    if((event.key==='ArrowDown' || event.key==='ArrowUp') && options.length){
      event.preventDefault();if(list.hidden)show();
      active=(active+(event.key==='ArrowDown'?1:-1)+options.length)%options.length;highlight();
    } else if(event.key==='Enter'){
      event.preventDefault();const pick=options[Math.max(active,0)];if(pick && !list.hidden)choose(pick);
    } else if(event.key==='Escape' && !list.hidden){event.preventDefault();close();}
  });
}

function update() {
  const config=collect(), result=validate(config,meta,table,berlinToday());
  for(const output of form.querySelectorAll('.field-error')){
    const key=output.id.replace(/-error$/,'');
    output.textContent=result.errors[key]||'';
  }
  for(const input of form.querySelectorAll('input[name],select[name]')){
    const key=['duration_hours','duration_minutes'].includes(input.name)?'max_direction_minutes':input.name;
    input.toggleAttribute('aria-invalid',Boolean(result.errors[key]));
  }
  $('origins-input').toggleAttribute('aria-invalid',Boolean(result.errors.origins));
  for(const input of $('display-names').querySelectorAll('input'))input.toggleAttribute('aria-invalid',Boolean(result.errors.display_names));
  const e=result.estimate;
  $('estimate').textContent=e
    ?`Etwa ${e.requests} Anfragen pro Suchlauf (Budget ${config.max_http_attempts_per_run}) · etwa ${Math.max(1,Math.round(e.seconds/60))} Minuten (Limit ${Math.floor(config.max_run_seconds/60)}) · ${e.days} Abflugtag${e.days===1?'':'e'} ab morgen`
    :'Die Schätzung erscheint, sobald alle Felder gültig sind.';
  const share=e?Math.min(1,Math.max(e.requests/config.max_http_attempts_per_run,e.seconds/config.max_run_seconds)):0;
  $('estimate-bar').style.width=`${Math.round(share*100)}%`;
  $('estimate-bar').className=share>1||result.errors.estimate?'over':share>0.8?'high':'';
  const changed=changedFields(meta.config,config);
  const list=$('changes');list.replaceChildren();
  for(const key of changed){
    const item=node('li');
    item.append(node('strong',meta.labels[key]||key),node('span',`${shownValue(key,meta.config[key],meta)} → ${shownValue(key,config[key],meta)}`));
    list.append(item);
  }
  if(!changed.length)list.append(node('li','Noch keine Änderung gegenüber der aktuellen Suche.','changes-empty'));
  const messages=$('messages');messages.replaceChildren();
  const errorCount=Object.keys(result.errors).length;
  if(result.errors.estimate)messages.append(node('p',result.errors.estimate,'message error'));
  else if(errorCount)messages.append(node('p',`${errorCount} Feld${errorCount===1?'':'er'} bitte korrigieren.`,'message error'));
  for(const warning of result.warnings)messages.append(node('p',warning,'message warn'));
  if(changed.some(key=>scopeFields.includes(key)))
    messages.append(node('p','Ziel, Reiseklasse, Gepäck, getrennte Tickets oder Flugdauer-Limit ändern sich: Preisverlauf und Preisalarme beginnen für diese Suche neu. Der alte Verlauf bleibt gespeichert.','message info'));
  if(changed.some(key=>['origins','destination','departure_start','departure_end','min_trip_days','max_trip_days'].includes(key)))
    messages.append(node('p','Favoriten außerhalb der neuen Suche werden auf der Website automatisch entfernt.','message info'));
  $('submit').setAttribute('aria-disabled',String(Boolean(errorCount) || !changed.length));
  return {config,result,changed};
}

function showCurrent() {
  const c=meta.config, place=airportInfo(table,c.destination);
  $('current-search').textContent=`Aktuell: ${c.origins.join(', ')} → ${c.destination}${place?` (${c.display_names[c.destination]||place.city})`:''} · Abflug ${germanDate(c.departure_start)} bis ${germanDate(c.departure_end)} · ${c.min_trip_days}–${c.max_trip_days} Tage · ${meta.travel_classes[c.travel_class]}`;
}

form.addEventListener('input',event=>{if(!event.target.closest('.combo'))update();});
form.addEventListener('change',event=>{if(!event.target.closest('.combo'))update();});
form.addEventListener('reset',()=>setTimeout(()=>{fill(meta.config);$('submit-note').replaceChildren();},0));
form.addEventListener('submit',event=>{
  event.preventDefault();
  const {config,result,changed}=update(), note=$('submit-note');note.replaceChildren();
  const invalid=Object.keys(result.errors).filter(key=>key!=='estimate');
  if(invalid.length){
    note.textContent='Bitte zuerst die markierten Felder korrigieren.';
    const first=form.querySelector('[aria-invalid]');if(first)first.focus();
    return;
  }
  if(result.errors.estimate){note.textContent=result.errors.estimate;return;}
  if(!changed.length){note.textContent='Keine Änderung gegenüber der aktuellen Suche.';return;}
  const url=issueUrl(meta,config);
  window.open(url,'_blank','noopener');
  const link=node('a','Issue auf GitHub öffnen ↗');link.href=url;link.target='_blank';link.rel='noopener noreferrer';
  note.append('Auf GitHub öffnet sich ein vorausgefülltes Issue. Klicke dort auf „Create“. Etwa eine Minute später antwortet der Workflow im Issue, und die neue Suche startet. Falls sich kein Tab geöffnet hat: ',link);
});

async function load() {
  try {
    const [settings,airports]=await Promise.all(['./search-config.json','./airports.json'].map(async url=>{
      const response=await fetch(url,{cache:'no-store'});if(!response.ok)throw new Error('Fetch failed');return response.json();
    }));
    if(settings.version!==1 || !settings.config || !airports.airports)throw new Error('Invalid data');
    meta=settings;table=airports;
    numberFields=[...Object.keys(meta.limits.int),...Object.keys(meta.limits.float),...priceFields]
      .filter(name=>!['max_direction_minutes','carry_on_bags','checked_bags'].includes(name));
    for(const [name,[low,high]] of Object.entries({...meta.limits.int,...meta.limits.float})){
      const input=field(name);if(input instanceof HTMLInputElement){input.min=low;input.max=high;}
    }
    field('travel_class').replaceChildren(...Object.entries(meta.travel_classes).map(([value,label])=>{const o=node('option',label);o.value=value;return o;}));
    field('departure_start').min=field('departure_end').min=berlinToday();
    combo('origins');combo('destination');
    fill(meta.config);showCurrent();form.hidden=false;
  } catch {
    $('current-search').textContent='Aktuelle Suche konnte nicht geladen werden.';
    $('load-error').hidden=false;
    $('load-error').textContent='Die Einstellungen konnten nicht geladen werden. Bitte später erneut versuchen oder config.json direkt auf GitHub bearbeiten.';
  }
}
load();

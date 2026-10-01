import {airportInfo, searchAirports, searchAirlines, validate, changedFields, shownValue, orderedConfig, issueUrl, scopeFields, priceFields} from './search-model.mjs';

const $ = id => document.getElementById(id);
const form = $('settings');
const field = name => form.elements.namedItem(name);
const node = (tag, text, cls) => {const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n;};
const berlinToday = () => new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Berlin'}).format(new Date());
const shortDate = value => new Intl.DateTimeFormat('en-GB',{day:'numeric',month:'short',year:'numeric',timeZone:'UTC'}).format(new Date(value+'T12:00:00Z'));
const numberValue = value => String(value).trim()==='' ? NaN : Number(value);
let meta, table, state, numberFields;

function cityOf(code) {return airportInfo(table,code)?.city || code;}
function routeCodes() {return [...new Set([...state.origins,state.destination])];}

// One chip picker per list setting: airports from the airport list, airlines from the airline list.
const pickers = {
  origins: {multiple:true, search:q=>searchAirports(table,q,30), code:x=>x.code,
    label:x=>`${x.city} (${x.code})`, detail:x=>[x.name,x.country].filter(Boolean).join(' · '),
    chip:code=>{const x=airportInfo(table,code);return x?`${x.city}${x.country?' · '+x.country:''}`:'unknown';}, name:cityOf},
  destination: {multiple:false, search:q=>searchAirports(table,q,30), code:x=>x.code,
    label:x=>`${x.city} (${x.code})`, detail:x=>[x.name,x.country].filter(Boolean).join(' · '),
    chip:code=>{const x=airportInfo(table,code);return x?`${x.city}${x.country?' · '+x.country:''}`:'unknown';}, name:cityOf},
};
pickers.airlines = {multiple:true, search:q=>searchAirlines(meta.airlines,q,30), code:x=>x.code,
  label:x=>`${x.name} (${x.code})`, detail:()=>'', chip:code=>meta.airlines[code]||'unknown', name:code=>meta.airlines[code]||code};
pickers.airlines_exclude = pickers.airlines;

function selected(kind) {return kind==='destination'?[state.destination]:state[kind];}

function fill(config) {
  state={origins:[...config.origins],destination:config.destination,airlines:[...config.airlines],
    airlines_exclude:[...config.airlines_exclude],names:{...config.display_names}};
  for(const name of [...numberFields,'departure_start','departure_end','travel_class'])field(name).value=config[name];
  field('max_stops').value=config.max_stops===null?'':String(config.max_stops);
  field('duration_hours').value=Math.floor(config.max_direction_minutes/60);
  field('duration_minutes').value=config.max_direction_minutes%60;
  field('hide_separate_tickets').checked=config.hide_separate_tickets;
  field('carry_on_bags').checked=config.carry_on_bags===1;
  field('checked_bags').checked=config.checked_bags===1;
  renderChips();renderNames();update();
}

function collect() {
  const config={...meta.config,origins:[...state.origins],destination:state.destination,
    airlines:[...state.airlines],airlines_exclude:[...state.airlines_exclude]};
  for(const name of ['departure_start','departure_end','travel_class'])config[name]=field(name).value;
  for(const name of numberFields)config[name]=numberValue(field(name).value);
  config.max_stops=field('max_stops').value===''?null:Number(field('max_stops').value);
  const hours=numberValue(field('duration_hours').value), minutes=numberValue(field('duration_minutes').value);
  config.max_direction_minutes=Number.isInteger(hours) && Number.isInteger(minutes) && hours>=0 && minutes>=0 && minutes<60 ? hours*60+minutes : NaN;
  config.hide_separate_tickets=field('hide_separate_tickets').checked;
  config.carry_on_bags=field('carry_on_bags').checked?1:0;
  config.checked_bags=field('checked_bags').checked?1:0;
  config.display_names=Object.fromEntries(routeCodes().map(code=>[code,(state.names[code]||'').trim()])
    .filter(([code,name])=>name && name!==cityOf(code)));
  return orderedConfig(meta,config);
}

function renderChips() {
  for(const [kind,picker] of Object.entries(pickers)) {
    const chips=selected(kind).map(code=>{
      const item=node('li',undefined,'chip');
      item.append(node('strong',code),node('span',picker.chip(code)));
      if(picker.multiple){
        const remove=node('button','✕','chip-remove');remove.type='button';
        remove.setAttribute('aria-label',`Remove ${picker.name(code)} (${code})`);
        remove.addEventListener('click',()=>{state[kind]=state[kind].filter(c=>c!==code);renderChips();renderNames();update();$(`${kind}-input`).focus();});
        item.append(remove);
      }
      return item;
    });
    $(`${kind}-chips`).replaceChildren(...chips);
  }
}

function renderNames() {
  const box=$('display-names');box.replaceChildren();
  for(const code of routeCodes()) {
    const label=node('label',`Name for ${code}`,'field'), input=node('input');
    input.type='text';input.maxLength=meta.limits.display_name;input.value=state.names[code]||'';
    input.placeholder=`automatic: ${cityOf(code)}`;input.dataset.code=code;
    input.setAttribute('aria-describedby','display_names-error');
    input.addEventListener('input',()=>{state.names[code]=input.value;});
    label.append(input);box.append(label);
  }
}

function combo(kind) {
  const picker=pickers[kind], input=$(`${kind}-input`), list=$(`${kind}-list`);
  let options=[], active=-1;
  const close=()=>{list.hidden=true;input.setAttribute('aria-expanded','false');input.removeAttribute('aria-activedescendant');active=-1;};
  const highlight=()=>{
    [...list.children].forEach((item,i)=>{item.setAttribute('aria-selected',String(i===active));item.classList.toggle('active',i===active);});
    if(active>=0){input.setAttribute('aria-activedescendant',`${kind}-option-${active}`);list.children[active].scrollIntoView({block:'nearest'});}
  };
  const choose=option=>{
    const code=picker.code(option);
    if(picker.multiple){if(!state[kind].includes(code))state[kind].push(code);}
    else state[kind]=code;
    input.value='';close();renderChips();renderNames();update();input.focus();
  };
  const show=()=>{
    const query=input.value.trim(), taken=selected(kind);
    options=picker.search(query).filter(option=>!taken.includes(picker.code(option))).slice(0,8);
    list.replaceChildren();
    options.forEach((option,i)=>{
      const item=node('li');item.id=`${kind}-option-${i}`;item.setAttribute('role','option');item.setAttribute('aria-selected','false');
      item.append(node('strong',picker.label(option)));
      if(picker.detail(option))item.append(node('small',picker.detail(option)));
      item.addEventListener('mousedown',event=>event.preventDefault());
      item.addEventListener('click',()=>choose(option));
      list.append(item);
    });
    if(query && !options.length){const empty=node('li','No supported match found.','suggestion-empty');empty.setAttribute('aria-disabled','true');list.append(empty);}
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
  for(const kind of Object.keys(pickers))$(`${kind}-input`).toggleAttribute('aria-invalid',Boolean(result.errors[kind]));
  for(const input of $('display-names').querySelectorAll('input'))input.toggleAttribute('aria-invalid',Boolean(result.errors.display_names));
  const e=result.estimate;
  $('estimate').textContent=e
    ?`About ${e.requests} requests per run (budget ${config.max_http_attempts_per_run}) · about ${Math.max(1,Math.round(e.seconds/60))} min (limit ${Math.floor(config.max_run_seconds/60)}) · ${e.days} departure day${e.days===1?'':'s'} from tomorrow`
    :'The estimate appears once every field is valid.';
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
  if(!changed.length)list.append(node('li','No change from the current search yet.','changes-empty'));
  const messages=$('messages');messages.replaceChildren();
  const errorCount=Object.keys(result.errors).length;
  if(result.errors.estimate)messages.append(node('p',result.errors.estimate,'message error'));
  else if(errorCount)messages.append(node('p',`Please correct ${errorCount} field${errorCount===1?'':'s'}.`,'message error'));
  for(const warning of result.warnings)messages.append(node('p',warning,'message warn'));
  if(changed.some(key=>scopeFields.includes(key)))
    messages.append(node('p','Settings that define comparable prices change (destination, cabin, bags, separate tickets, travel time limit, airlines or stops): price history and alerts start over for this search. The old history stays stored.','message info'));
  if(changed.some(key=>['origins','destination','departure_start','departure_end','min_trip_days','max_trip_days'].includes(key)))
    messages.append(node('p','Favorites outside the new search are removed from the website automatically.','message info'));
  $('submit').setAttribute('aria-disabled',String(Boolean(errorCount) || !changed.length));
  return {config,result,changed};
}

function showCurrent() {
  const c=meta.config, place=airportInfo(table,c.destination);
  $('current-search').textContent=`Current: ${c.origins.join(', ')} → ${c.destination}${place?` (${c.display_names[c.destination]||place.city})`:''} · departures ${shortDate(c.departure_start)} to ${shortDate(c.departure_end)} · ${c.min_trip_days}–${c.max_trip_days} days · ${meta.travel_classes[c.travel_class]}`;
}

form.addEventListener('input',event=>{if(!event.target.closest('.combo'))update();});
form.addEventListener('change',event=>{if(!event.target.closest('.combo'))update();});
form.addEventListener('reset',()=>setTimeout(()=>{fill(meta.config);$('submit-note').replaceChildren();},0));
form.addEventListener('submit',event=>{
  event.preventDefault();
  const {config,result,changed}=update(), note=$('submit-note');note.replaceChildren();
  const invalid=Object.keys(result.errors).filter(key=>key!=='estimate');
  if(invalid.length){
    note.textContent='Please correct the marked fields first.';
    const first=form.querySelector('[aria-invalid]');if(first)first.focus();
    return;
  }
  if(result.errors.estimate){note.textContent=result.errors.estimate;return;}
  if(!changed.length){note.textContent='No change from the current search.';return;}
  const url=issueUrl(meta,config);
  window.open(url,'_blank','noopener');
  const link=node('a','Open the issue on GitHub ↗');link.href=url;link.target='_blank';link.rel='noopener noreferrer';
  note.append('A prefilled issue opens on GitHub. Click “Create” there. About a minute later the workflow replies in the issue and the new search starts. If no tab opened: ',link);
});

async function load() {
  try {
    const [settings,airports]=await Promise.all(['./search-config.json','./airports.json'].map(async url=>{
      const response=await fetch(url,{cache:'no-store'});if(!response.ok)throw new Error('Fetch failed');return response.json();
    }));
    if(settings.version!==1 || !settings.config || !settings.airlines || !airports.airports)throw new Error('Invalid data');
    meta=settings;table=airports;
    numberFields=[...Object.keys(meta.limits.int),...Object.keys(meta.limits.float),...priceFields]
      .filter(name=>!['max_direction_minutes','carry_on_bags','checked_bags'].includes(name));
    for(const [name,[low,high]] of Object.entries({...meta.limits.int,...meta.limits.float})){
      const input=field(name);if(input instanceof HTMLInputElement){input.min=low;input.max=high;}
    }
    field('travel_class').replaceChildren(...Object.entries(meta.travel_classes).map(([value,label])=>{const o=node('option',label);o.value=value;return o;}));
    field('departure_start').min=field('departure_end').min=berlinToday();
    for(const kind of Object.keys(pickers))combo(kind);
    fill(meta.config);showCurrent();form.hidden=false;
  } catch {
    $('current-search').textContent='The current search could not be loaded.';
    $('load-error').hidden=false;
    $('load-error').textContent='The settings could not be loaded. Please try again later or edit config.json directly on GitHub.';
  }
}
load();

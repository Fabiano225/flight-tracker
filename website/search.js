import {airportInfo, searchAirports, searchAirlines, validateTrips, changedFields, settingsJson, settingsChanges, issueUrl, issueBody, MAX_URL, scopeFields, priceFields} from './search-model.mjs';

const $ = id => document.getElementById(id);
const form = $('settings');
const field = name => form.elements.namedItem(name);
const node = (tag, text, cls) => {const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(cls)n.className=cls; return n;};
const berlinToday = () => new Intl.DateTimeFormat('en-CA',{timeZone:'Europe/Berlin'}).format(new Date());
const shortDate = value => new Intl.DateTimeFormat('en-GB',{day:'numeric',month:'short',year:'numeric',timeZone:'UTC'}).format(new Date(value+'T12:00:00Z'));
const numberValue = value => String(value).trim()==='' ? NaN : Number(value);
const routeFields = ['origins','destination','departure_start','departure_end','min_trip_days','max_trip_days'];
// meta: the saved settings (search-config.json). trips: the edited trips, each
// {key, id (null for a new trip), config}; one of them is shown in the form.
let meta, table, numberFields, fieldOrder, trips=[], active=0, primaryKey, nextKey=1, state, lastResult;

function cityOf(code) {return airportInfo(table,code)?.city || code;}
function routeCodes() {return [...new Set([...state.origins,state.destination].filter(Boolean))];}
const configs = () => trips.map(trip=>trip.config);
const primaryIndex = () => Math.max(0,trips.findIndex(trip=>trip.key===primaryKey));
function tripLabel(trip) {
  const config=trip.config;
  if(!config.destination)return 'New trip';
  const name=config.display_names[config.destination]||cityOf(config.destination);
  const twins=trips.filter(other=>other.config.destination===config.destination);
  return twins.length>1?`${name} (${twins.indexOf(trip)+1})`:name;
}

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

function selected(kind) {return kind==='destination'?(state.destination?[state.destination]:[]):state[kind];}

// Show one trip in the form. Shared request settings are the same for every trip.
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
  renderChips();renderNames();
}

function collect() {
  const config={...trips[active].config,origins:[...state.origins],destination:state.destination,
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
  // Keep the field order of config.json so the stored file stays readable.
  return Object.fromEntries(fieldOrder.map(key=>[key,config[key]]));
}

// Store the form into the shown trip and copy the shared settings to every trip.
function sync() {
  const config=collect();
  trips[active].config=config;
  const shared=Object.fromEntries(meta.shared_fields.map(key=>[key,config[key]]));
  for(const trip of trips)trip.config={...trip.config,...shared};
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

function renderTrips() {
  // The buttons are rebuilt; keep keyboard focus on the trip bar.
  const hadFocus=$('trip-tabs').contains(document.activeElement);
  const tabs=trips.map((trip,i)=>{
    const button=node('button',undefined,'trip-choice');button.type='button';
    const problems=lastResult && (Object.keys(lastResult.trips[i]||{}).length>0);
    button.append(node('strong',tripLabel(trip)),node('small',[trip.config.destination||'—',trip.id?null:'new',trip.key===primaryKey&&trips.length>1?'shown first':null,problems?'needs a fix':null].filter(Boolean).join(' · ')));
    button.setAttribute('aria-pressed',String(i===active));
    if(problems)button.classList.add('has-error');
    button.addEventListener('click',()=>switchTrip(i));
    return button;
  });
  $('trip-tabs').replaceChildren(...tabs);
  if(hadFocus)tabs[active].focus();
  const primary=$('primary-trip');
  primary.replaceChildren(...trips.map(trip=>{const option=node('option',tripLabel(trip));option.value=String(trip.key);return option;}));
  primary.value=String(primaryKey);
  // Choosing the first trip or removing one makes sense only with several trips.
  $('trip-tools').hidden=trips.length<2;
  $('add-trip').disabled=trips.length>=meta.max_trips;
  $('add-trip').textContent=trips.length>=meta.max_trips?`${meta.max_trips} trips at most`:'+ Add trip';
}

function switchTrip(index) {
  if(index===active)return;
  sync();active=index;fill(trips[active].config);update();
  $('trips-title').scrollIntoView({block:'nearest'});
}

function addTrip() {
  if(trips.length>=meta.max_trips)return;
  sync();
  // A new trip starts as a copy of the trip shown, without destination and names.
  const base=trips[active].config;
  trips.push({key:nextKey++,id:null,config:{...structuredClone(base),destination:'',display_names:{}}});
  active=trips.length-1;fill(trips[active].config);update();
  $('trip-note').textContent='New trip: settings copied from the trip you were editing. Choose a destination and check the dates and price targets.';
  $('destination-input').focus();
}

function removeTrip() {
  if(trips.length<2)return;
  const [removed]=trips.splice(active,1);
  if(removed.key===primaryKey)primaryKey=trips[0].key;
  active=Math.max(0,active-1);fill(trips[active].config);update();
  $('trip-note').textContent=`${tripLabel(removed)==='New trip'?'The new trip':tripLabel(removed)} removed. Reset brings it back until you apply.`;
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
  sync();
  const result=validateTrips(configs(),meta,table,berlinToday()), errors={...result.trips[active],...result.errors};
  lastResult=result;
  for(const output of form.querySelectorAll('.field-error')){
    const key=output.id.replace(/-error$/,'');
    output.textContent=errors[key]||'';
  }
  for(const input of form.querySelectorAll('input[name],select[name]')){
    const key=['duration_hours','duration_minutes'].includes(input.name)?'max_direction_minutes':input.name;
    input.toggleAttribute('aria-invalid',Boolean(errors[key]));
  }
  for(const kind of Object.keys(pickers))$(`${kind}-input`).toggleAttribute('aria-invalid',Boolean(errors[kind]));
  for(const input of $('display-names').querySelectorAll('input'))input.toggleAttribute('aria-invalid',Boolean(errors.display_names));
  renderTrips();
  const e=result.estimate, budget=trips[0].config, several=trips.length>1;
  $('estimate').textContent=e
    ?`About ${e.requests} requests per run${several?` for ${trips.length} trips (this trip about ${e.trips[active].requests})`:''} · budget ${budget.max_http_attempts_per_run} · about ${Math.max(1,Math.round(e.seconds/60))} min (limit ${Math.floor(budget.max_run_seconds/60)})`+
      (several?'':` · ${e.trips[0].days} departure day${e.trips[0].days===1?'':'s'} from tomorrow`)
    :'The estimate appears once every field of every trip is valid.';
  const share=e?Math.min(1,Math.max(e.requests/budget.max_http_attempts_per_run,e.seconds/budget.max_run_seconds)):0;
  $('estimate-bar').style.width=`${Math.round(share*100)}%`;
  $('estimate-bar').className=share>1||result.errors.estimate?'over':share>0.8?'high':'';
  const changes=settingsChanges(meta,trips,primaryIndex());
  const list=$('changes');list.replaceChildren();
  for(const change of changes){
    const item=node('li');
    item.append(node('strong',change.label),node('span',`${change.before} → ${change.after}`));
    list.append(item);
  }
  if(!changes.length)list.append(node('li','No change from the current search yet.','changes-empty'));
  const messages=$('messages');messages.replaceChildren();
  const fieldErrors=result.trips.reduce((total,trip)=>total+Object.keys(trip).length,0)+Object.keys(result.errors).filter(key=>key!=='estimate').length;
  const elsewhere=trips.filter((_,i)=>i!==active && Object.keys(result.trips[i]).length).map(tripLabel);
  if(result.errors.estimate)messages.append(node('p',result.errors.estimate,'message error'));
  else if(fieldErrors)messages.append(node('p',`Please correct ${fieldErrors} field${fieldErrors===1?'':'s'}${elsewhere.length?` (also in: ${elsewhere.join(', ')})`:''}.`,'message error'));
  for(const warning of result.warnings)messages.append(node('p',warning,'message warn'));
  // Saved trips whose comparable-price settings change start a new price history.
  const saved=new Map(meta.trips.map(config=>[config.id,config]));
  const restarted=trips.filter(trip=>trip.id && saved.has(trip.id) && changedFields(saved.get(trip.id),trip.config).some(key=>scopeFields.includes(key)));
  if(restarted.length)
    messages.append(node('p',`Settings that define comparable prices change${several||meta.trips.length>1?` for ${restarted.map(tripLabel).join(', ')}`:''} (destination, cabin, bags, separate tickets, travel time limit, airlines or stops): price history and alerts start over for ${restarted.length===1?(several?'this trip':'this search'):'these trips'}. The old history stays stored.`,'message info'));
  const moved=trips.some(trip=>trip.id && saved.has(trip.id) && changedFields(saved.get(trip.id),trip.config).some(key=>routeFields.includes(key)));
  if(moved || meta.trips.some(config=>!trips.some(trip=>trip.id===config.id)))
    messages.append(node('p','Favorites outside the new search are removed from the website automatically.','message info'));
  $('submit').setAttribute('aria-disabled',String(Boolean(fieldErrors) || Boolean(result.errors.estimate) || !changes.length));
  return {result,changes,fieldErrors};
}

function showCurrent() {
  const box=$('current-search');box.replaceChildren();
  const line=c=>{const place=airportInfo(table,c.destination);
    return `${c.origins.join(', ')} → ${c.destination}${place?` (${c.display_names[c.destination]||place.city})`:''} · departures ${shortDate(c.departure_start)} to ${shortDate(c.departure_end)} · ${c.min_trip_days}–${c.max_trip_days} days · ${meta.travel_classes[c.travel_class]}`;};
  if(meta.trips.length===1){box.textContent=`Current: ${line(meta.trips[0])}`;return;}
  const list=node('ol');
  for(const config of meta.trips)list.append(node('li',line(config)+(config.id===meta.primary_trip?' · shown first':'')));
  box.append(node('span',`Current: ${meta.trips.length} trips`),list);
}

function start() {
  trips=meta.trips.map(config=>({key:nextKey++,id:config.id,config:structuredClone(config)}));
  primaryKey=trips.find(trip=>trip.id===meta.primary_trip)?.key??trips[0].key;
  // settings.html?trip=ams opens that trip; the dashboard links here with it.
  const wanted=new URLSearchParams(location.search).get('trip');
  active=Math.max(0,trips.findIndex(trip=>trip.id===(wanted||meta.primary_trip)));
  fill(trips[active].config);update();
}

const edited=event=>{
  if(event.target.id==='primary-trip')primaryKey=Number(event.target.value);
  if(!event.target.closest('.combo'))update();
};
form.addEventListener('input',edited);
form.addEventListener('change',edited);
form.addEventListener('reset',()=>setTimeout(()=>{start();$('submit-note').replaceChildren();$('copy-body').hidden=true;
  $('trip-note').textContent='All trips are back to the current settings.';},0));
$('add-trip').addEventListener('click',addTrip);
$('remove-trip').addEventListener('click',removeTrip);
$('copy-button').addEventListener('click',async()=>{
  const text=$('issue-body');text.select();
  try{await navigator.clipboard.writeText(text.value);$('copy-button').textContent='Copied ✓';}catch{$('copy-button').textContent='Press Ctrl+C to copy';}
});
form.addEventListener('submit',event=>{
  event.preventDefault();
  const {result,changes,fieldErrors}=update(), note=$('submit-note');note.replaceChildren();$('copy-body').hidden=true;
  if(fieldErrors){
    note.textContent='Please correct the marked fields first.';
    const broken=result.trips.findIndex(errors=>Object.keys(errors).length);
    if(broken>=0 && broken!==active)switchTrip(broken);
    const first=form.querySelector('[aria-invalid]');if(first)first.focus();
    return;
  }
  if(result.errors.estimate){note.textContent=result.errors.estimate;return;}
  if(!changes.length){note.textContent='No change from the current search.';return;}
  const settings=settingsJson(meta,trips,primaryIndex());
  let url=issueUrl(meta,settings,changes);
  if(url.length>MAX_URL) {
    // Too long for a link: open an empty issue and let the text be pasted.
    url=issueUrl(meta,settings,changes,false);
    $('issue-body').value=issueBody(meta,settings,changes);$('copy-body').hidden=false;$('copy-button').textContent='Copy text';
  }
  window.open(url,'_blank','noopener');
  const link=node('a','Open the issue on GitHub ↗');link.href=url;link.target='_blank';link.rel='noopener noreferrer';
  note.append($('copy-body').hidden
    ?'A prefilled issue opens on GitHub. Click “Create” there. About a minute later the workflow replies in the issue and the new search starts. If no tab opened: '
    :'These settings are too long for a link. Copy the text below, paste it as the issue description on GitHub and click “Create”: ',link);
});

async function load() {
  try {
    const [settings,airports]=await Promise.all(['./search-config.json','./airports.json'].map(async url=>{
      const response=await fetch(url,{cache:'no-store'});if(!response.ok)throw new Error('Fetch failed');return response.json();
    }));
    if(settings.version!==2 || !Array.isArray(settings.trips) || !settings.trips.length || !settings.airlines || !airports.airports)throw new Error('Invalid data');
    meta=settings;table=airports;fieldOrder=Object.keys(meta.trips[0]);
    numberFields=[...Object.keys(meta.limits.int),...Object.keys(meta.limits.float),...priceFields]
      .filter(name=>!['max_direction_minutes','carry_on_bags','checked_bags'].includes(name));
    for(const [name,[low,high]] of Object.entries({...meta.limits.int,...meta.limits.float})){
      const input=field(name);if(input instanceof HTMLInputElement){input.min=low;input.max=high;}
    }
    field('travel_class').replaceChildren(...Object.entries(meta.travel_classes).map(([value,label])=>{const o=node('option',label);o.value=value;return o;}));
    field('departure_start').min=field('departure_end').min=berlinToday();
    for(const kind of Object.keys(pickers))combo(kind);
    start();showCurrent();form.hidden=false;
  } catch {
    $('current-search').textContent='The current search could not be loaded.';
    $('load-error').hidden=false;
    $('load-error').textContent='The settings could not be loaded. Please try again later or edit config.json directly on GitHub.';
  }
}
load();

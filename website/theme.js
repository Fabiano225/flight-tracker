// Runs before the first paint: applies the chosen color theme. "auto" follows the
// system setting; the choice is kept in this browser only.
(()=>{
  const key='flightwatch:theme', root=document.documentElement;
  const media=window.matchMedia?matchMedia('(prefers-color-scheme: dark)'):null;
  const read=()=>{
    try{const value=localStorage.getItem(key);return value==='light' || value==='dark'?value:'auto';}catch{return 'auto';}
  };
  let choice=read();
  const apply=()=>{
    const theme=choice==='auto'?(media?.matches?'dark':'light'):choice;
    root.dataset.themeChoice=choice;root.dataset.theme=theme;root.style.colorScheme=theme;
    for(const select of document.querySelectorAll('.theme-select'))select.value=choice;
  };
  apply();
  media?.addEventListener?.('change',apply);
  // Another tab changed the theme.
  window.addEventListener('storage',event=>{if(event.key===key || event.key===null){choice=read();apply();}});
  document.addEventListener('DOMContentLoaded',()=>{
    for(const select of document.querySelectorAll('.theme-select'))select.addEventListener('change',()=>{
      choice=['auto','light','dark'].includes(select.value)?select.value:'auto';
      // Without storage (private window) the choice lasts until the page is left.
      try{if(choice==='auto')localStorage.removeItem(key);else localStorage.setItem(key,choice);}catch{}
      apply();
    });
    apply();
  });
})();

/* Styloteka standalone offline-first library (read only). */
(() => {
  'use strict';
  const K = {games:'styloteka_public_games_offline_v45',upcoming:'styloteka_public_events_offline_v45_upcoming',history:'styloteka_public_events_offline_v45_history'};
  const $ = id=>document.getElementById(id);
  const state={games:[],upcoming:[],history:[],savedAt:{},active:'tools',scope:'upcoming',deferredInstall:null};
  const storageGet = key=>{try{return JSON.parse(localStorage.getItem(key)||'null')}catch{return null}};
  const storageSet=(key,value)=>{try{localStorage.setItem(key,JSON.stringify(value));return true}catch{return false}};
  const safeText=s=>String(s??'');
  const msg=s=>{$('syncMessage').textContent=s};
  const publicEvent=e=>Object.fromEntries(['id','title','description','event_date','event_time','game_name','game_names','location_text','cover_url','price_kop','max_participants','status'].filter(k=>Object.hasOwn(e,k)).map(k=>[k,e[k]]));
  const stored= (key,items)=>storageSet(K[key],{savedAt:Date.now(),items});
  const dateLabel=n=>{if(!n)return'немає';try{return new Date(n).toLocaleString('uk-UA',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'})}catch{return'збережено'}};
  const dateFull=d=>{if(!d)return'Дату не вказано';const a=String(d).split('-');return a.length===3?`${a[2]}.${a[1]}.${a[0]}`:String(d)};
  function hydrate(){
    for(const key of ['games','upcoming','history']) {
      const doc=storageGet(K[key]);
      if(Array.isArray(doc?.items)){state[key]=doc.items;state.savedAt[key]=doc.savedAt||0;}
    }
  }
  function status(){
    const online=navigator.onLine;
    const saved=Math.max(...Object.values(state.savedAt),0);
    const el=$('connStatus');el.classList.toggle('offline',!online);
    el.textContent=online?'🟢 З’єднання є — можна оновити дані':'📴 Немає інтернету — працюємо зі збереженим';
    const small=document.createElement('small');small.textContent=saved?`Останнє збереження: ${dateLabel(saved)}`:'Поки нічого не збережено. Відкрий сторінку з інтернетом і натисни «Оновити дані».';el.appendChild(small);
    $('syncBtn').disabled=!online;
    $('saveCoversBtn').disabled=!online||!state.games.length;
    $('gamesCount').textContent=state.games.length?`(${state.games.length})`:'';
    $('eventsCount').textContent=state.upcoming.length?`(${state.upcoming.length})`:'';
  }
  function showInfo(title,description,extra=''){
    $('detailTitle').textContent=title||'Деталі';$('detailBody').textContent=[description,extra].filter(Boolean).join('\n\n');$('detailDialog').showModal();
  }
  function imageNode(item, name){
    const url=safeText(item.cover_url);
    if(!/^https?:\/\//.test(url) && !url.startsWith('/')) {const d=document.createElement('div');d.className='card-art';d.textContent='🎲';return d;}
    const img=document.createElement('img');img.className='card-cover';img.loading='lazy';img.alt=`Обкладинка: ${name}`;img.src=url;
    img.addEventListener('error',()=>{const d=document.createElement('div');d.className='card-art';d.textContent='🎲';img.replaceWith(d);},{once:true});
    return img;
  }
  function addCard(parent,item,mode){
    const name=safeText(mode==='games'?item.name:item.title)||'Без назви';
    const el=document.createElement('article');el.className='card';
    el.appendChild(imageNode(item,name));
    const body=document.createElement('div');body.className='card-body';
    const h=document.createElement('h3');h.textContent=name;body.appendChild(h);
    const tag=document.createElement('div');tag.className='tag';
    tag.textContent=mode==='games'?safeText(item.tags||'Настільна гра') : `${dateFull(item.event_date)}${item.event_time?' · '+String(item.event_time).slice(0,5):''}`;
    body.appendChild(tag);
    const desc=document.createElement('div');desc.className='card-desc';desc.textContent=safeText(item.description||'Опис ще не додано');body.appendChild(desc);
    if(mode!=='games') {const meta=document.createElement('div');meta.className='card-meta';meta.textContent=`${Number(item.price_kop)>0?Number(item.price_kop)/100+' грн':'Безкоштовно'}${item.location_text?' · '+String(item.location_text):''}`;body.appendChild(meta);}
    const actions=document.createElement('div');actions.className='card-actions';
    const b=document.createElement('button');b.className='btn secondary small';b.textContent='Детальніше';b.addEventListener('click',()=>{
      const extra=mode==='games'?(item.rules_url?'Правила за посиланням: '+item.rules_url+'\n(потрібен інтернет)':''):
        [`Дата: ${dateFull(item.event_date)} ${safeText(item.event_time)}`,
         `Місце: ${safeText(item.location_text||'уточнюйте онлайн')}`,
         `Вартість: ${Number(item.price_kop)>0?Number(item.price_kop)/100+' грн':'безкоштовно'}`,
         item.game_names?`Заплановані ігри: ${Array.isArray(item.game_names)?item.game_names.join(', '):String(item.game_names)}`:''].filter(Boolean).join('\n');
      showInfo(name,safeText(item.description||'Опис відсутній'),extra);
    });actions.appendChild(b);body.appendChild(actions);el.appendChild(body);parent.appendChild(el);
  }
  function renderGames(){
    const q=$('searchGames').value.trim().toLocaleLowerCase('uk');
    const rows=state.games.filter(x=>`${x.name||''} ${x.tags||''} ${x.description||''}`.toLocaleLowerCase('uk').includes(q));
    $('gamesMeta').textContent=`Знайдено ${rows.length} із ${state.games.length} ігор · ${state.savedAt.games?'збережено '+dateLabel(state.savedAt.games):'дані не завантажено'}`;
    const root=$('gamesGrid');root.replaceChildren();
    if(!rows.length){const x=document.createElement('div');x.className='empty';x.textContent=state.games.length?'За цим запитом ігор не знайдено.':'Каталог ще не збережено. Підключись до інтернету й натисни «Оновити дані».';root.appendChild(x);return;}
    const fragment=document.createDocumentFragment();for(const game of rows) addCard(fragment,game,'games');root.appendChild(fragment);
  }
  function renderEvents(){
    const q=$('searchEvents').value.trim().toLocaleLowerCase('uk');
    const key=state.scope;
    const rows=state[key].filter(x=>`${x.title||''} ${x.description||''} ${x.game_name||''} ${x.location_text||''}`.toLocaleLowerCase('uk').includes(q));
    $('eventsMeta').textContent=`Показано ${rows.length} · ${state.savedAt[key]?'збережено '+dateLabel(state.savedAt[key]):'дані не завантажено'}`;
    const root=$('eventsGrid');root.replaceChildren();
    if(!rows.length){const e=document.createElement('div');e.className='empty';e.textContent=state[key].length?'Нічого не знайдено.':'Список подій ще не збережено.';root.appendChild(e);return;}
    const fragment=document.createDocumentFragment();for(const e of rows) addCard(fragment,e,'events');root.appendChild(fragment);
  }
  function render(){status();renderGames();renderEvents();}
  async function updateOne(key,endpoint){
    // SW cache may return public content offline — only invoke network sync if browser reports online.
    const r=await fetch(endpoint,{cache:'no-store'});
    if(!r.ok || r.headers.get('X-Styloteka-Offline-Empty')==='1')throw new Error(`HTTP ${r.status}`);
    const json=await r.json();if(!Array.isArray(json))throw new Error('Invalid server response');
    const data=key==='games'?json:json.map(publicEvent);
    state[key]=data;state.savedAt[key]=Date.now();stored(key,data);return data.length;
  }
  let syncing=false;
  async function sync(){
    if(syncing||!navigator.onLine)return;syncing=true;$('syncBtn').disabled=true;msg('Завантажуємо публічні дані…');
    const jobs=[['games','/api/games'],['upcoming','/api/events?scope=upcoming'],['history','/api/events?scope=history']];
    const results=await Promise.allSettled(jobs.map(([key,url])=>updateOne(key,url)));
    syncing=false;render();
    const ok=results.filter(x=>x.status==='fulfilled').length;
    msg(ok===3?'✅ Усе збережено на цьому пристрої. Можна користуватися офлайн.':ok?`⚠️ Збережено ${ok} з 3 розділів. Інші показують останню локальну копію.`:'⚠️ Не вдалося отримати дані з сервера. Збережені копії не видалено.');
  }
  async function saveCovers(){
    if(!navigator.onLine||!('caches' in window))return msg('Збереження обкладинок недоступне на цьому пристрої.');
    const urls=[...new Set([...state.games,...state.upcoming,...state.history].map(x=>x.cover_url).filter(u=>/^https?:\/\//.test(String(u||''))))].slice(0,35);
    if(!urls.length)return msg('У бібліотеці немає доступних обкладинок.');
    const btn=$('saveCoversBtn');btn.disabled=true;
    let saved=0,failed=0;const cache=await caches.open('styloteka-allowed-covers-v1');
    for(const url of urls){
      try{
        const response=await fetch(url,{mode:'no-cors',cache:'force-cache'});
        if(response.ok||response.type==='opaque'){await cache.put(url,response.clone());saved++;}else failed++;
      }catch(_){failed++;}
      msg(`Збереження обкладинок: ${saved+failed}/${urls.length}`);
    }
    btn.disabled=false;msg(`🖼️ Обкладинки: збережено ${saved}, недоступно ${failed}. Решта карток покажуть 🎲.`);
  }
  const selectTab=kind=>{
    state.active=kind;
    $('tabTools').setAttribute('aria-selected',String(kind==='tools'));
    $('tabGames').setAttribute('aria-selected',String(kind==='games'));
    $('tabEvents').setAttribute('aria-selected',String(kind==='events'));
    $('panelTools').classList.toggle('hidden',kind!=='tools');
    $('panelGames').classList.toggle('hidden',kind!=='games');
    $('panelEvents').classList.toggle('hidden',kind!=='events');
  };
  function init(){
    hydrate();render();
    $('syncBtn').addEventListener('click',sync);$('saveCoversBtn').addEventListener('click',saveCovers);
    $('searchGames').addEventListener('input',renderGames);$('searchEvents').addEventListener('input',renderEvents);
    $('tabTools').addEventListener('click',()=>selectTab('tools'));
    $('tabGames').addEventListener('click',()=>selectTab('games'));$('tabEvents').addEventListener('click',()=>selectTab('events'));
    $('eventScope').addEventListener('change',e=>{state.scope=e.target.value;renderEvents()});
    $('randomBtn').addEventListener('click',()=>{
      const q=$('searchGames').value.trim().toLocaleLowerCase('uk');const rows=state.games.filter(x=>`${x.name||''} ${x.tags||''} ${x.description||''}`.toLocaleLowerCase('uk').includes(q));
      if(!rows.length)return msg('Немає завантажених ігор для випадкового вибору.');
      const selected=rows[Math.floor(Math.random()*rows.length)];showInfo('🎲 '+safeText(selected.name),safeText(selected.description||'Немає опису'),safeText(selected.tags||''));
    });
    $('detailClose').addEventListener('click',()=>$('detailDialog').close());
    $('detailDialog').addEventListener('click',e=>{if(e.target===$('detailDialog'))e.target.close()});
    window.addEventListener('online',()=>{status();sync()});window.addEventListener('offline',()=>{status();msg('Офлайн: показуємо збережені дані.')});
    window.addEventListener('beforeinstallprompt',e=>{e.preventDefault();state.deferredInstall=e;$('installBtn').hidden=false});
    $('installBtn').addEventListener('click',async()=>{const p=state.deferredInstall;if(!p)return;await p.prompt();state.deferredInstall=null;$('installBtn').hidden=true});
    if('serviceWorker' in navigator&&window.isSecureContext){navigator.serviceWorker.register('/sw.js',{scope:'/'}).catch(e=>console.warn('PWA offline unavailable',e))}
    if(navigator.onLine)sync();else msg('Офлайн: показуємо дані, збережені раніше.');
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();

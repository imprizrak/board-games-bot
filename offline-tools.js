/* Styloteka v4.6 — interactive offline game tools. No network calls. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const SCORES = 'styloteka_counter_session_v46';
  const FLIP = 'styloteka_flip_local_v46';
  const TIMER = 'styloteka_turn_timer_v46';
  const pickerPalette = ['#f5cc6c','#7fbbf3','#a2e9b3','#f7a9c7','#c1a5fa','#ffb99a','#8fe5dc','#e3b1f5'];
  const toInt=(value, fallback=0)=>Number.isFinite(Number(value))?Math.trunc(Number(value)):fallback;
  const safeName=(s, fallback='Гравець')=>String(s || fallback).slice(0,64);
  const load=(key)=>{try{return JSON.parse(localStorage.getItem(key)||'null')}catch(_){return null}};
  const store=(key,val)=>{try{localStorage.setItem(key,JSON.stringify(val));return true}catch(_){return false}};
  const randomInt = max => {
    if (max < 1) return 0;
    if (window.crypto?.getRandomValues) {
      const array=new Uint32Array(1); const span=0x100000000;
      const limit=span-span%max;
      do {window.crypto.getRandomValues(array)} while(array[0]>=limit);
      return array[0]%max;
    }
    return Math.floor(Math.random()*max);
  };
  const button=(text,cls,handler,label)=>{
    const b=document.createElement('button');b.type='button';b.textContent=text;b.className=cls;
    if(label)b.setAttribute('aria-label',label);
    b.addEventListener('click',handler);return b;
  };

  /* Shared score list: the online counter view reads this same local key. */
  function validScoreDoc(raw){
    if(!Array.isArray(raw?.players)) return null;
    const players=[];const ids=new Set();
    for(const it of raw.players.slice(0,32)){
      const id=toInt(it?.id,-1),score=toInt(it?.score);
      if(id<1||ids.has(id)||Math.abs(score)>99999999) continue;
      ids.add(id);players.push({id,name:safeName(it.name,'Гравець '+id),score});
    }
    return players.length?{players,nextId:Math.max(...players.map(p=>p.id+1),toInt(raw.nextId,0))}:null;
  }
  let scores=validScoreDoc(load(SCORES))||{
    players:[{id:1,name:'Гравець 1',score:0},{id:2,name:'Гравець 2',score:0}],nextId:3
  };
  let history=[];
  const saveScores=()=>{const ok=store(SCORES,{players:scores.players,nextId:scores.nextId,updatedAt:Date.now()});
    $('scoreSavedText').textContent=ok?'✓ Збережено на цьому пристрої. Очки доступні й у звичайних лічильниках Styloteka.':'⚠️ Браузер не дозволяє зберегти дані. Не закривай сторінку.';
  };
  const remember=()=>{history.push(JSON.stringify(scores));if(history.length>30)history.shift()};
  function refreshCounters(){
    const el=$('offlineCounterRows');el.replaceChildren();
    scores.players.forEach(player=>{
      const row=document.createElement('div');row.className='score-player';
      const input=document.createElement('input');input.className='score-name';input.value=player.name;input.maxLength=64;
      input.setAttribute('aria-label','Ім’я гравця');
      input.addEventListener('input',()=>{
        player.name=safeName(input.value,`Гравець ${player.id}`);saveScores();
        const bs=row.querySelectorAll('button');
        if(bs[0])bs[0].setAttribute('aria-label',`Мінус одне очко: ${player.name}`);
        if(bs[1])bs[1].setAttribute('aria-label',`Плюс одне очко: ${player.name}`);
        if(bs[2])bs[2].setAttribute('aria-label',`Видалити: ${player.name}`);
      });
      input.addEventListener('change',()=>{renderPickChecks();renderFlip();});
      row.append(input);
      row.append(button('−','score-btn minus',()=>changeScore(player.id,-1),`Мінус одне очко: ${player.name}`));
      const val=document.createElement('strong');val.className='score-num';val.textContent=String(player.score);row.append(val);
      row.append(button('+','score-btn plus',()=>changeScore(player.id,1),`Плюс одне очко: ${player.name}`));
      row.append(button('×','score-del',()=>removePlayer(player.id),`Видалити: ${player.name}`));
      el.append(row);
    });
    $('offlineUndoScore').disabled=!history.length;
    renderPickChecks();
  }
  function changeScore(id,amount){
    const player=scores.players.find(p=>p.id===id);if(!player)return;
    remember();player.score=Math.max(-99999999,Math.min(99999999,player.score+amount));saveScores();refreshCounters();
  }
  function removePlayer(id){
    if(scores.players.length===1){$('scoreSavedText').textContent='Залиш хоча б одного гравця.';return}
    const p=scores.players.find(p=>p.id===id);if(!p||!confirm(`Видалити гравця «${p.name}»?`))return;
    remember();scores.players=scores.players.filter(p=>p.id!==id);saveScores();refreshCounters();renderFlip();
  }
  $('offlineAddPlayer').addEventListener('click',()=>{
    if(scores.players.length>=32){$('scoreSavedText').textContent='Максимум 32 гравці на одному телефоні.';return}
    remember();const id=scores.nextId++;scores.players.push({id,name:`Гравець ${id}`,score:0});saveScores();refreshCounters();renderFlip();
  });
  $('offlineUndoScore').addEventListener('click',()=>{
    if(!history.length)return;const doc=validScoreDoc(JSON.parse(history.pop()));if(doc)scores=doc;
    saveScores();refreshCounters();renderFlip();
  });
  $('offlineResetScore').addEventListener('click',()=>{
    if(!confirm('Обнулити очки всіх гравців?'))return;
    remember();scores.players.forEach(p=>p.score=0);saveScores();refreshCounters();
  });

  /* Touch / mouse / pen first-player picker, fully on-device. */
  let enabledIds=new Set(scores.players.map(p=>p.id));
  const contacts=new Map();let outcome=null;let selectionTimeout=null;let pickerCounter=0;
  function renderPickChecks(){
    const root=$('pickerPlayerChecks');root.replaceChildren();
    const currentIds=new Set(scores.players.map(p=>p.id));
    enabledIds=new Set([...enabledIds].filter(id=>currentIds.has(id)));
    if(!enabledIds.size)enabledIds=new Set(currentIds);
    scores.players.forEach(p=>{
      const label=document.createElement('label');label.className='pick-check';
      const cb=document.createElement('input');cb.type='checkbox';cb.checked=enabledIds.has(p.id);
      cb.addEventListener('change',()=>{if(cb.checked)enabledIds.add(p.id);else enabledIds.delete(p.id);resetPicker()});
      const span=document.createElement('span');span.textContent=p.name;
      label.append(cb,span);root.append(label);
    });
  }
  function pickerNames(){return scores.players.filter(p=>enabledIds.has(p.id))}
  function resetPicker(){contacts.clear();outcome=null;pickerCounter=0;clearTimeout(selectionTimeout);selectionTimeout=null;
    $('pickerWinner').textContent='Очікуємо гравців…';paintPicker()}
  const pad=$('offlinePickerArea');
  function paintPicker(){
    pad.querySelectorAll('.picker-dot').forEach(x=>x.remove());
    const hint=$('offlinePickerHint');hint.style.display=contacts.size?'none':'';
    contacts.forEach(c=>{
      const dot=document.createElement('span');dot.className='picker-dot'+(outcome===c.id?' winner-dot':'');
      dot.style.left=`${c.x}%`;dot.style.top=`${c.y}%`;dot.style.setProperty('--dot',c.color);
      dot.textContent=c.player?.name||`Палець ${c.seq}`;pad.append(dot);
    });
    if(!outcome&&contacts.size>=2)$('pickerWinner').textContent=`${contacts.size} гравці на полі · утримуйте пальці…`;
  }
  function scheduleWinner(){
    clearTimeout(selectionTimeout);selectionTimeout=null;
    if(outcome||contacts.size<2)return;
    selectionTimeout=setTimeout(()=>{
      if(contacts.size<2||outcome)return;
      const ids=[...contacts.keys()];outcome=ids[randomInt(ids.length)];
      const selected=contacts.get(outcome);
      $('pickerWinner').textContent=`🏆 Першим грає: ${selected?.player?.name||`Палець ${selected?.seq||1}`}!`;
      paintPicker();
    },2000);
  }
  const padPos=e=>{const rect=pad.getBoundingClientRect();return {x:Math.max(9,Math.min(91,(e.clientX-rect.left)/rect.width*100)),y:Math.max(14,Math.min(86,(e.clientY-rect.top)/rect.height*100))}};
  pad.addEventListener('pointerdown',e=>{
    if(e.button!==0||contacts.has(e.pointerId))return;
    e.preventDefault();if(outcome)resetPicker();
    if(contacts.size>=10)return;
    try{pad.setPointerCapture(e.pointerId)}catch(_){}
    const seq=pickerCounter++;const selection=pickerNames();
    contacts.set(e.pointerId,{id:e.pointerId,seq,player:selection[seq]||null,color:pickerPalette[seq%pickerPalette.length],...padPos(e)});
    paintPicker();scheduleWinner();
  });
  pad.addEventListener('pointermove',e=>{const c=contacts.get(e.pointerId);if(!c)return;Object.assign(c,padPos(e));paintPicker()});
  function release(e){if(!contacts.has(e.pointerId))return;contacts.delete(e.pointerId);clearTimeout(selectionTimeout);selectionTimeout=null;
    if(!contacts.size){pickerCounter=0;if(!outcome)$('pickerWinner').textContent='Покладіть 2+ пальці на поле.'}
    paintPicker();scheduleWinner();}
  for(const t of ['pointerup','pointercancel','lostpointercapture'])pad.addEventListener(t,release);
  $('offlinePickRandom').addEventListener('click',()=>{const eligible=pickerNames();if(!eligible.length){$('pickerWinner').textContent='Познач хоча б одного гравця.';return}
    const w=eligible[randomInt(eligible.length)];$('pickerWinner').textContent=`🏆 Першим грає: ${w.name}!`;});
  $('offlinePickReset').addEventListener('click',resetPicker);
  refreshCounters();

  /* Dice */
  const sides=[4,6,8,10,12,20,100];let currentDie=6;let rolls=[];
  function diceOptions(){const root=$('diceButtons');root.replaceChildren();sides.forEach(s=>root.append(button(`d${s}`,'die-choice'+(currentDie===s?' selected':''),()=>{currentDie=s;diceOptions()},`Кубик із ${s} гранями`)))}
  $('offlineRoll').addEventListener('click',()=>{const value=randomInt(currentDie)+1;rolls.unshift(`d${currentDie}: ${value}`);rolls=rolls.slice(0,15);
    $('offlineDiceResult').textContent=String(value);$('diceHistory').textContent=rolls.join(' · ');});
  $('offlineClearRolls').addEventListener('click',()=>{rolls=[];$('offlineDiceResult').textContent='🎲';$('diceHistory').textContent='Історію очищено.'});
  diceOptions();

  /* Manual Flip Seven score keeper, distinct from server-based XP and tournaments. */
  let flip=load(FLIP);
  if(!flip||!Array.isArray(flip.players))flip={round:1,players:[]};
  flip.players=flip.players.filter(p=>Number.isInteger(Number(p.id))).slice(0,32).map(p=>({id:toInt(p.id),name:safeName(p.name),total:Math.max(0,toInt(p.total)),pendingDelta:Number.isFinite(Number(p.pendingDelta))&&p.pendingDelta!==null?Math.max(0,toInt(p.pendingDelta)):null}));
  flip.round=Math.max(1,toInt(flip.round,1));let flipHistory=[];
  function flipSyncRoster(){
    if(!flip.players.length)flip.players=scores.players.map(p=>({id:p.id,name:p.name,total:0,pendingDelta:null}));
    for(const fp of flip.players){const p=scores.players.find(x=>x.id===fp.id);if(p)fp.name=p.name}
    // never remove existing scores if a player is removed from simple counters
  }
  function flipSave(){store(FLIP,{round:flip.round,players:flip.players})}
  function renderFlip(){
    flipSyncRoster();flipSave();$('flipRoundLabel').textContent=`Раунд ${flip.round}`;
    const root=$('flipPlayers');root.replaceChildren();const sel=$('flipTarget');const old=sel.value;sel.replaceChildren();
    flip.players.forEach(p=>{
      const row=document.createElement('div');row.className='flip-row';
      const name=document.createElement('div');name.textContent=p.name+(p.pendingDelta===null?'':` · +${p.pendingDelta} цього раунду`);
      const score=document.createElement('strong');score.textContent=String(p.total);
      row.append(name,score,button('×','score-del',()=>{
        if(flip.players.length<=1||!confirm(`Прибрати ${p.name} з рахунку Flip Seven?`))return;
        flipHistory.push(JSON.stringify(flip));flip.players=flip.players.filter(x=>x.id!==p.id);renderFlip();
      },`Прибрати ${p.name} з Flip Seven`));
      root.append(row);
      const option=document.createElement('option');option.value=String(p.id);option.textContent=p.name+(p.pendingDelta===null?'':` (записано)`);sel.append(option);
    });
    if([...sel.options].some(o=>o.value===old))sel.value=old;
    $('flipUndo').disabled=!flipHistory.length;
  }
  $('flipAddPlayer').addEventListener('click',()=>{
    if(flip.players.length>=32)return;
    flipHistory.push(JSON.stringify(flip));
    const ids=new Set(flip.players.map(x=>x.id));
    const p=scores.players.find(x=>!ids.has(x.id));
    const id=p?.id||Math.max(...flip.players.map(x=>x.id),0)+1;
    flip.players.push({id,name:p?.name||`Гравець ${id}`,total:0,pendingDelta:null});renderFlip();
  });
  function flipCommit(bust=false){
    const p=flip.players.find(x=>x.id===toInt($('flipTarget').value,-1));if(!p)return;
    const points=toInt($('flipInput').value,0);
    if(points<0||points>5000){alert('Введи число очок від 0 до 5000.');return}
    const score=bust?0:points*($('flipDouble').checked?2:1)+($('flipBonus').checked?15:0);
    flipHistory.push(JSON.stringify(flip));if(flipHistory.length>30)flipHistory.shift();
    p.pendingDelta=score;
    $('flipInput').value='0';$('flipDouble').checked=false;$('flipBonus').checked=false;
    renderFlip();
    const next=flip.players.find(x=>x.pendingDelta===null);if(next)$('flipTarget').value=String(next.id);
  }
  $('flipCommit').addEventListener('click',()=>flipCommit(false));$('flipBust').addEventListener('click',()=>flipCommit(true));
  $('flipRoundEnd').addEventListener('click',()=>{
    if(flip.players.some(p=>p.pendingDelta===null) && !confirm('Не всі гравці записали очки. Решті поставити 0 і завершити раунд?'))return;
    flipHistory.push(JSON.stringify(flip));if(flipHistory.length>30)flipHistory.shift();
    flip.players.forEach(p=>{p.total+=p.pendingDelta||0;p.pendingDelta=null});flip.round++;renderFlip();
  });
  $('flipUndo').addEventListener('click',()=>{if(!flipHistory.length)return;flip=JSON.parse(flipHistory.pop());renderFlip()});
  renderFlip();

  /* Offline turn timer, accurate across background sleep. */
  const timerConfig=load(TIMER);
  let timer={remainingMs:120000,endAt:0,running:false};
  if(timerConfig&&Number.isFinite(timerConfig.remainingMs))timer.remainingMs=Math.max(0,Math.min(10800000,timerConfig.remainingMs));
  if(timerConfig?.running&&Number.isFinite(timerConfig.endAt)){
    timer.endAt=timerConfig.endAt;
    timer.running=Date.now()<timer.endAt;
    timer.remainingMs=Math.max(0,timer.endAt-Date.now());
  }
  const timeStore=()=>store(TIMER,timer);
  const pad2=n=>String(n).padStart(2,'0');
  function updateClock(){
    if(timer.running){timer.remainingMs=Math.max(0,timer.endAt-Date.now());if(!timer.remainingMs){timer.running=false;timeStore()}}
    const seconds=Math.ceil(timer.remainingMs/1000),minutes=Math.floor(seconds/60);
    $('turnClock').textContent=`${pad2(minutes)}:${pad2(seconds%60)}`;
    $('turnClock').classList.toggle('time-up',timer.remainingMs===0);
    $('turnStart').disabled=timer.running;
    $('turnPause').disabled=!timer.running;
  }
  function configMs(){const m=toInt($('turnMinutes').value,2),s=toInt($('turnSeconds').value,0);
    return (Math.max(0,Math.min(180,m))*60+Math.max(0,Math.min(59,s)))*1000}
  $('turnStart').addEventListener('click',()=>{
    if(timer.remainingMs<=0)timer.remainingMs=configMs();
    if(timer.remainingMs<=0){alert('Укажи час таймера.');return}
    timer.endAt=Date.now()+timer.remainingMs;timer.running=true;timeStore();updateClock();
  });
  $('turnPause').addEventListener('click',()=>{if(!timer.running)return;timer.remainingMs=Math.max(0,timer.endAt-Date.now());timer.running=false;timeStore();updateClock()});
  $('turnReset').addEventListener('click',()=>{timer.running=false;timer.endAt=0;timer.remainingMs=configMs();timeStore();updateClock()});
  setInterval(updateClock,250);window.addEventListener('visibilitychange',updateClock);updateClock();

  /* Tool tabs within the offline app. */
  document.querySelectorAll('.tool-switch').forEach(b=>b.addEventListener('click',()=>{
    document.querySelectorAll('.tool-switch').forEach(x=>{const active=x===b;x.classList.toggle('active',active);x.setAttribute('aria-pressed',String(active))});
    document.querySelectorAll('.tool-view').forEach(x=>x.classList.toggle('hidden',x.id!==`tool-${b.dataset.tool}`));
    if(b.dataset.tool==='score')refreshCounters();
  }));
  window.addEventListener('storage',event=>{if(event.key!==SCORES)return;
    const doc=validScoreDoc(load(SCORES));if(doc){scores=doc;refreshCounters();renderFlip()}
  });
  document.addEventListener('visibilitychange',()=>{
    if(document.visibilityState==='visible'){
      const doc=validScoreDoc(load(SCORES));if(doc){scores=doc;refreshCounters();renderFlip()}
    }
  });
})();

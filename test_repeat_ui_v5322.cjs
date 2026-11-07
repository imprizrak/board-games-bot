const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const html = fs.readFileSync(__dirname + '/index.html','utf8');
const form = html.slice(html.indexOf('function populateEventFormFromEvent(ev) {'), html.indexOf('function openEventModal() {'));
const submit = html.slice(html.indexOf('async function submitEvent() {'), html.indexOf('// Private details and receipt moderation'));
assert.ok(form.length>1000 && submit.length>1000);
let fields = {};
const get = id => fields[id] || (fields[id]={value:'',required:false,style:{display:'none'},files:[],classList:{add:()=>{}}});
const alerts=[];
const fetchCalls=[];
let renderedGames=[];
const ctx={
  isAdmin:true,editingEventId:null,repeatSourceEventId:null,editingEventRemoveCover:false,
  eventsMode:'history',username:'admin',telegramInitData:'signed_auth',
  document:{getElementById:get}, Number, Date, String, JSON, console,
  setEventModalMode: e=>{get('eventModalHeading').textContent=e?'Редагувати подію':'Нова подія';get('eventSubmitBtn').textContent=e?'Зберегти зміни':'Створити подію'},
  setEventDateField:v=>get('eventDate').value=v,
  setEventPriceMode:()=>{},setEventQuestsVisibility:()=>{},
  eventGameNames:ev=>ev.game_names || [], renderEventGameChoices:n=>{renderedGames=n},
  resetEventCoverPreview:()=>{},initEventCoverPreview:()=>{},showExistingEventCover:url=>{get('eventCoverPreview').src=url},
  resetEventDatePickerField:()=>get('eventDate').value='',
  tg:{showAlert:s=>alerts.push(s)},
  FormData:class {constructor(){this.values={}} append(k,v){this.values[k]=v}},
  getSelectedEventGames:()=>renderedGames,
  normalizeEventJarLink:v=>v,
  parseEventDateValue:v=>new Date(v+'T00:00:00'),
  telegramAuthHeaders:()=>({'X-Telegram-InitData':'valid'}),
  fetch:async (u,opts)=> {fetchCalls.push([u,opts]);return {ok:true,status:200,json:async()=>({event_id:91})}},
  closeModal:()=>{}, loadEvents:mode=>{ctx.loadedMode=mode}
};
vm.createContext(ctx);
vm.runInContext(form+'\n'+submit,ctx);
const completed={id:15,status:'completed',title:'Граємо знову',event_date:'2026-11-01',event_time:'19:00',
  location_text:'Приватна адреса',description:'Зустріч друзів',game_names:['Каркассон','Селестія'],
  price_kop:15000,payment_method:'receipt',payment_link:'https://send.monobank.ua/jar/ABCDEFG',payment_details:'Ім’я у коментарі',
  cover_url:'https://example.org/old.jpg',max_participants:8,quests_event_url:'https://quests.invalid/old',quests_count:8,quests_checked_in_count:7};
ctx.openRepeatEventModal(completed);
assert.equal(ctx.editingEventId,null,'must be a new event, not edit');
assert.equal(ctx.repeatSourceEventId,15);
assert.equal(get('eventTitle').value,completed.title);
assert.equal(get('eventTime').value,'19:00');
assert.equal(get('eventDate').value,'','old date must not carry over');
assert.equal(get('eventQuestsUrl').value,'','old external signup must not carry over');
assert.equal(get('eventQuestsCount').value,'0');
assert.equal(get('eventQuestsCheckedInCount').value,'0');
assert.equal(get('eventPaymentLink').value,completed.payment_link);
assert.equal(get('eventCoverPreview').src,completed.cover_url);
assert.deepEqual(renderedGames,completed.game_names);
assert.equal(get('eventRepeatHint').style.display,'block');
ctx.submitEvent().then(async()=>{
 assert.equal(fetchCalls.length,0,'missing date must prevent submit');
 assert.match(alerts[0],/нову дату/);
 get('eventDate').value='2099-05-21';
 await ctx.submitEvent();
 assert.equal(fetchCalls.length,1);
 assert.equal(fetchCalls[0][0],'/api/events');
 assert.equal(fetchCalls[0][1].method,'POST');
 const data=fetchCalls[0][1].body.values;
 assert.equal(data.repeat_source_event_id,'15');
 assert.equal(data.event_date,'2099-05-21');
 assert.equal(data.event_time,'19:00');
 assert.equal(data.price_uah,'150.00');
 assert.equal(data.quests_event_url,'');
 assert.equal(data.quests_count,'0');
 assert.equal(ctx.loadedMode,'upcoming','new event should be shown');
 assert.equal(ctx.repeatSourceEventId,null);
 console.log('Repeat UI tests passed');
}).catch(e=>{console.error(e);process.exit(1)});

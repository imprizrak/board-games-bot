"""Test existing + new game autofill; no network/API credentials, no production writes."""
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).parent
html=(ROOT/'index.html').read_text(encoding='utf-8')
js=Path(ROOT/'app.py').read_text(encoding='utf-8')
assert 'gameAutofillMode=\'edit\'' in html
assert 'editGameAutofillBox' in html
assert 'cover_source_url' in js
assert '_admin_required_response()' in js
MOCK=r'''
window.Telegram={WebApp:{initData:'test-telegram-session',initDataUnsafe:{user:{id:123,first_name:'Admin'}},
 platform:'ios',version:'8.0',isFullscreen:false,isVersionAtLeast(){return true},
 ready(){},expand(){},requestFullscreen(){},onEvent(){},setHeaderColor(){},setBackgroundColor(){},setBottomBarColor(){},
 HapticFeedback:{impactOccurred(){},notificationOccurred(){},selectionChanged(){}},
 showAlert(s){window.__alert=s},showConfirm(s,cb){cb(false)},
 MainButton:{show(){},hide(){},setText(){},onClick(){},offClick(){},enable(){},disable(){}}}};
window.__savedGameForms=[];
const resultGame={
 id:1120,name:'Wingspan',tags:'1–5 гравців, 40–70 хв, Тварини',
 description:'Настільна гра «Wingspan» для 1–5 гравців. Орієнтовна тривалість — 40–70 хв.',
 cover_url:'https://cf.geekdo-images.com/sample-cover.jpg',bgg_url:'https://boardgamegeek.com/boardgame/266192',
};
window.fetch=async(url,options={})=>{
 const target=String(url),post=options.body||'';
 if(target.includes('/api/games/autofill')) {
   const req=JSON.parse(post);
   return {ok:true,status:200,json:async()=>req.bgg_id?
      {game:resultGame,videos:[{title:'Огляд українською',url:'https://www.youtube.com/watch?v=abc12345678'}],
       rules:[{title:'Правила видавця',url:'https://publisher.example/rules.pdf'}],
       search_links:{bgg:'https://boardgamegeek.com/geeksearch.php?q=Wingspan'}}:
      {candidates:[{id:266192,name:'Wingspan',year:2019}],provider:'BoardGameGeek',configured:true}};
 }
 if(target.match(/\/api\/games\/\d+$/) && options.method==='PUT'){
   let f={};for (const [k,v] of post.entries()) f[k]=typeof v==='string'?v:'<uploaded file>';
   window.__savedGameForms.push(f);
   return {ok:true,status:200,json:async()=>({status:'updated'})};
 }
 return {ok:true,status:200,json:async()=>([]),text:async()=>'[]',headers:{get(){return null}}};
};
'''
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
 for width,height in [(320,700),(375,812),(393,852),(430,932)]:
  page=browser.new_page(viewport={'width':width,'height':height},is_mobile=True,has_touch=True,device_scale_factor=1)
  for domain in ['https://fonts.googleapis.com/**','https://fonts.gstatic.com/**','https://telegram.org/**']:
   page.route(domain,lambda r:r.abort())
  errs=[];page.on('pageerror',lambda e:errs.append(str(e)))
  page.set_content(html,wait_until='domcontentloaded')
  page.evaluate(MOCK)
  page.evaluate('''() => {
    isAdmin=true;
    currentGame={id:8,name:'Крила',description:'Мій власний опис.\\n\\n🎥 Відеоогляд: https://www.youtube.com/watch?v=oldVidAbc12',
      tags:'Авторські теги',rules_url:'https://original.example/rules.pdf',cover_url:'https://original.example/cover.png'};
    openEditModal();
  }''')
  assert page.locator('#editModal').evaluate("e=>e.classList.contains('open')")
  assert page.locator('#editDescription').input_value()=='Мій власний опис.'
  assert page.locator('#editVideoUrl').input_value().endswith('oldVidAbc12')
  page.locator('#editGameAutofillButton').click()
  page.locator('#editGameAutofillMatches button').first.wait_for()
  page.locator('#editGameAutofillMatches button').first.click()
  page.locator('#editGameAutofillPreview .game-autofill-apply').wait_for()
  assert page.locator('#editGameAutofillPreview input[data-autofill-field]:checked').count()==0
  assert page.locator('#editGameAutofillPreview input[data-autofill-field]').count()==6
  page.locator('#editGameAutofillPreview input[data-autofill-field="rules"]').check()
  page.locator('#editGameAutofillPreview input[data-autofill-field="video"]').check()
  page.locator('#editGameAutofillPreview .game-autofill-apply').click()
  assert page.locator('#editName').input_value()=='Крила', 'Existing local name should stay'
  assert page.locator('#editTags').input_value()=='Авторські теги'
  assert page.locator('#editDescription').input_value()=='Мій власний опис.'
  assert page.locator('#editRulesUrl').input_value()=='https://publisher.example/rules.pdf'
  assert page.locator('#editVideoUrl').input_value()=='https://www.youtube.com/watch?v=abc12345678'
  assert page.locator('#editGameAutofillCoverUrl').input_value()==''
  assert page.evaluate('window.__savedGameForms.length')==0
  page.evaluate('submitEdit()')
  page.wait_for_function('window.__savedGameForms.length===1')
  form=page.evaluate('window.__savedGameForms[0]')
  assert form['name']=='Крила'
  assert form['tags']=='Авторські теги'
  assert form['rules_url']=='https://publisher.example/rules.pdf'
  assert 'Мій власний опис.' in form['description']
  assert form['description'].count('🎥 Відеоогляд:')==1,form['description']
  assert 'cover_source_url' not in form
  # New-game mode still works and requests same verified BGG API.
  page.evaluate('openAddModal()')
  page.locator('#inputName').fill('Wingspan')
  page.locator('#gameAutofillButton').click()
  page.locator('#gameAutofillMatches button').first.wait_for()
  page.locator('#gameAutofillMatches button').first.click()
  page.locator('#gameAutofillPreview .game-autofill-apply').wait_for()
  page.locator('#gameAutofillPreview .game-autofill-apply').click()
  assert page.locator('#inputDescription').input_value().startswith('Настільна гра')
  assert page.locator('#gameAutofillCoverUrl').input_value().startswith('https://cf.geekdo-images.com/')
  assert page.locator('#inputVideoUrl').input_value().startswith('https://www.youtube.com/')
  assert not errs, (width,errs)
  scroll=page.evaluate('''() => ({doc:document.documentElement.scrollWidth,win:innerWidth})''')
  assert scroll['doc']<=scroll['win']+3,(width,scroll)
  if width==393:
   page.locator('#editModal').evaluate("e=>e.classList.add('open')")
   page.locator('#addModal').evaluate("e=>e.classList.remove('open')")
   page.locator('#editGameAutofillPreview').scroll_into_view_if_needed()
   page.screenshot(path=str(ROOT/'preview_existing_game_autofill_393.png'))
  page.close()
  print('PASS existing edit/new add/field preservation',width)
 browser.close()

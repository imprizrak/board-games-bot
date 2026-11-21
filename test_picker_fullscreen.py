"""Offline interaction + layout regression for the iPhone Telegram player picker."""
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).parent
html=ROOT.joinpath('index.html').read_text('utf8')
assert 'styloteka-picker-overlay-v5335' in html
assert 'tg.requestFullscreen()' not in html
assert 'id="pickerProfiles"' not in html
assert 'id="diceProfileSelect"' not in html
assert 'id="gamesGrid"' in html
assert 'id="eventDatePickerModal"' in html

with sync_playwright() as pw:
 browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
 for w,h in [(320,700),(375,812),(393,852),(430,932),(768,1024)]:
  page=browser.new_page(viewport={'width':w,'height':h},is_mobile=True,has_touch=True,device_scale_factor=1)
  errors=[]
  page.on('pageerror',lambda e:errors.append(str(e)))
  page.route('https://telegram.org/**',lambda r:r.abort())
  page.route('https://fonts.googleapis.com/**',lambda r:r.abort())
  page.route('https://fonts.gstatic.com/**',lambda r:r.abort())
  page.route('**/api/**',lambda r:r.fulfill(status=200,content_type='application/json',body='[]'))
  page.set_content(html,wait_until='domcontentloaded')
  page.evaluate('''() => {window.pickerRequestedNativeFullScreen=0;
    Telegram.WebApp.requestFullscreen=()=>{window.pickerRequestedNativeFullScreen++};}''')
  # Use the actual tools tab, not a synthetic class toggle.
  page.locator('.nav-btn[data-view="view-tools"]').click()
  page.locator('.tool-tab-btn[data-tool="picker"]').click()
  d=page.evaluate('''() => {
    const p=document.getElementById('panel-picker').getBoundingClientRect(),
      a=pickerArea.getBoundingClientRect(),intro=document.querySelector('.picker-intro').getBoundingClientRect(),
      ex=document.querySelector('.picker-exit').getBoundingClientRect(),
      reset=document.querySelector('.picker-reset').getBoundingClientRect();
    return {w:innerWidth,h:innerHeight,p:p.toJSON(),a:a.toJSON(),intro:intro.toJSON(),ex:ex.toJSON(),reset:reset.toJSON(),
      parent:document.getElementById('panel-picker').parentNode.tagName,
      navVisible:getComputedStyle(document.querySelector('.bottom-nav')).display,
      nativeCalls:window.pickerRequestedNativeFullScreen,
      topTarget:document.elementFromPoint(innerWidth/2,a.top+20)?.id,
      centerTarget:document.elementFromPoint(innerWidth/2,a.top+a.height/2)?.id,
      closeTarget:document.elementFromPoint(ex.left+ex.width/2,ex.top+ex.height/2)?.className?.baseVal||document.elementFromPoint(ex.left+ex.width/2,ex.top+ex.height/2)?.className};
  }''')
  assert abs(d['p']['left'])<=1 and abs(d['p']['right']-w)<=1,d
  assert abs(d['p']['height']-h)<=1,d
  assert d['parent']=='BODY' and d['navVisible']=='none' and d['nativeCalls']==0,d
  assert d['intro']['top']>=0 and d['intro']['bottom']<=d['a']['top'],d
  assert d['ex']['top']>=0 and d['ex']['right']<=w and d['ex']['bottom']<d['a']['top'],d
  assert d['a']['top']>=80 and d['a']['bottom']<d['reset']['top'],d
  assert d['topTarget']=='pickerArea' and d['centerTarget']=='pickerArea',d
  assert not page.locator('#pickerStatus').is_visible(),d
  if w==393:page.screenshot(path=str(ROOT/'preview_picker_fixed_393.png'))
  # Two simultaneous fingers trigger the same production touch listeners.
  state=page.evaluate('''() => {
    const a=pickerArea,r=a.getBoundingClientRect();
    const t1={identifier:111,clientX:r.left+Math.max(48,r.width*.25),clientY:r.top+Math.max(60,r.height*.3)};
    const t2={identifier:222,clientX:r.left+r.width*.74,clientY:r.top+Math.max(60,r.height*.63)};
    const ev=new Event('touchstart',{bubbles:true,cancelable:true});
    Object.defineProperty(ev,'changedTouches',{value:[t1,t2]});
    a.dispatchEvent(ev);
    return {dots:a.querySelectorAll('.touch-dot').length,active:Object.keys(activeTouches).length,status:pickerStatus.textContent};
  }''')
  assert state['dots']==2 and state['active']==2 and '2 гравців' in state['status'],(w,state)
  assert page.locator('#pickerStatus').is_visible(),w
  # Spin + release during animation. It must still announce exactly 1 winner.
  page.evaluate("spinAndPickWinner()")
  page.evaluate('''() => {const ev=new Event('touchend',{bubbles:true,cancelable:true});
    Object.defineProperty(ev,'changedTouches',{value:[{identifier:111},{identifier:222}]});
    pickerArea.dispatchEvent(ev);}''')
  page.wait_for_function("pickerLocked && document.querySelectorAll('#pickerArea .winner').length === 1",timeout=6000)
  assert 'Хід має цей гравець' in page.locator('#pickerStatus').inner_text(),w
  page.wait_for_function("!pickerLocked && document.querySelectorAll('#pickerArea .touch-dot').length===0",timeout=5500)
  # Tapping reset and close restores app navigation and DOM order.
  page.locator('.picker-reset').click()
  page.locator('.picker-exit').click()
  post=page.evaluate('''() => ({parent:document.getElementById('panel-picker').parentNode.id,
    nav:getComputedStyle(document.querySelector('.bottom-nav')).display,
    pickerClass:document.body.classList.contains('picker-fullscreen')})''')
  assert post['parent']=='view-tools' and post['nav']!='none' and not post['pickerClass'],(w,post)
  page.locator('.tool-tab-btn[data-tool="dice"]').click()
  assert page.locator('#panel-dice.active').is_visible()
  assert not any('is not defined' in x or 'Cannot read properties' in x for x in errors),errors[:4]
  print('PASS',w,h,'layout, touch pick, release, reset, close, dice; js_errors',len(errors))
  page.close()
 browser.close()
print('ALL PICKER SCREEN TESTS PASSED')

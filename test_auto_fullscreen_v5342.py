from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path(__file__).parent
html=(root/'index.html').read_text('utf-8')
mock='''
window.__tgEvents={};
window.__tgCallLog=[];
window.Telegram={WebApp:{
 initData:'query_id=test&user=123', initDataUnsafe:{user:{id:123,first_name:'Test'}},
 platform:'ios',version:'8.0',isFullscreen:false,
 safeAreaInset:{top:54,bottom:20},contentSafeAreaInset:{top:110,bottom:22},
 isVersionAtLeast(v){return v==='8.0';},
 onEvent(event,cb){(window.__tgEvents[event]??=[]).push(cb)},
 ready(){window.__tgCallLog.push('ready')},expand(){window.__tgCallLog.push('expand')},
 requestFullscreen(){window.__tgCallLog.push('requestFullscreen'); this.isFullscreen=true;
   for(const cb of window.__tgEvents.fullscreenChanged||[])cb();},
 exitFullscreen(){window.__tgCallLog.push('exitFullscreen');this.isFullscreen=false},
 setHeaderColor(){},setBackgroundColor(){},setBottomBarColor(){},
 HapticFeedback:{selectionChanged(){},impactOccurred(){},notificationOccurred(){}},
 showAlert(){},showConfirm(s,cb){cb(true)},
 MainButton:{show(){},hide(){},setText(){},onClick(){},offClick(){},enable(){},disable(){}}
}};
window.fetch=async()=>({ok:true,status:200,json:async()=>[],text:async()=>"[]",headers:{get(){return null}}});
'''
legacy=mock.replace("isVersionAtLeast(v){return v==='8.0';}","isVersionAtLeast(v){return false;}")
with sync_playwright() as pw:
 browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
 for w,h in [(320,700),(375,812),(393,852),(430,932),(768,1024)]:
  page=browser.new_page(viewport={'width':w,'height':h},is_mobile=True,has_touch=True)
  errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  page.add_init_script(mock)
  page.evaluate(mock)
  for url in ('https://telegram.org/**','https://fonts.googleapis.com/**','https://fonts.gstatic.com/**'):
   page.route(url,lambda route:route.abort())
  page.route('**/api/**',lambda route:route.fulfill(status=200,content_type='application/json',body='[]'))
  page.set_content(html,wait_until='domcontentloaded')
  page.wait_for_function("document.body.classList.contains('tg-native-fullscreen')")
  calls=page.evaluate('window.__tgCallLog')
  assert calls.count('requestFullscreen')==1,(w,calls)
  top=page.evaluate('parseFloat(getComputedStyle(document.body).paddingTop)')
  assert top>=110,(w,top)
  assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 2'),w
  page.locator('.nav-btn[data-view="view-tools"]').click()
  page.locator('.ios-widget-picker').click()
  assert page.locator('body.picker-fullscreen').count()==1,w
  assert page.evaluate('window.Telegram.WebApp.isFullscreen'),w
  assert 'exitFullscreen' not in page.evaluate('window.__tgCallLog'),w
  pbox=page.locator('.picker-exit').bounding_box()
  assert pbox and pbox['y']>=118,(w,pbox)
  if w==393:
   page.screenshot(path=str(root/'preview_fullscreen_picker_393.png'))
  assert not errors,(w,errors)
  page.close()
 # legacy telegram versions must not call requestFullscreen
 page=browser.new_page(viewport={'width':393,'height':852},is_mobile=True,has_touch=True)
 page.add_init_script(legacy)
 page.evaluate(legacy)
 for url in ('https://telegram.org/**','https://fonts.googleapis.com/**','https://fonts.gstatic.com/**'):
  page.route(url,lambda route:route.abort())
 page.route('**/api/**',lambda route:route.fulfill(status=200,content_type='application/json',body='[]'))
 page.set_content(html,wait_until='domcontentloaded')
 assert 'requestFullscreen' not in page.evaluate('window.__tgCallLog')
 assert not page.evaluate("document.body.classList.contains('tg-native-fullscreen')")
 assert page.evaluate('parseFloat(getComputedStyle(document.body).paddingTop)') < 110
 page.close();browser.close()
print('PASS: modern fullscreen + picker + legacy fallback on 5 viewport widths')

"""Playwright: click timestamp recorded, Kyiv display, legacy fallback and review UI."""
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).parent
HTML=(ROOT/'index.html').read_text('utf-8')
MOCK='''
window.__requests=[];
window.__alerts=[];
window.Telegram={WebApp:{initData:'query_id=test&user=%7B%22id%22%3A123%7D',
initDataUnsafe:{user:{id:123,username:'test',first_name:'Test'}},
ready(){},expand(){},onEvent(){},showAlert(m){window.__alerts.push(m)},
setHeaderColor(){},setBackgroundColor(){},setBottomBarColor(){},
HapticFeedback:{notificationOccurred(){},impactOccurred(){}},isExpanded:true,
safeAreaInset:{top:0,bottom:0},contentSafeAreaInset:{top:0,bottom:0}}};
window.fetch=async(url,options={})=>{
 const path=String(url),method=options.method||'GET';
 window.__requests.push({path,method});
 let value=[];
 if(path==='/api/events/21/join-intent')value={status:'recorded'};
 else if(path==='/api/events/21/receipt')value={status:'not_submitted',title:'Настолки',price_kop:10000,payment_link:'',payment_details:''};
 else if(path.startsWith('/api/admin/event-receipts?')) value={receipts:[
   {id:1,event_id:21,telegram_user_id:123,display_name:'Ірина',status:'pending',
    joined_at:'2026-11-22T12:35:00+00:00',created_at:'2026-11-22T12:42:00+00:00',reviewed_at:null},
   {id:2,event_id:21,telegram_user_id:124,display_name:'Олександр',status:'approved',
    joined_at:null,created_at:'2026-11-21T19:03:34Z',reviewed_at:'2026-11-21T22:37:41Z'}
 ]};
 else if(path.includes('/api/admins/check'))value={is_admin:true};
 else if(path.endsWith('/review'))value={status:'approved',private_message_sent:true};
 return {ok:true,status:200,json:async()=>value,text:async()=>JSON.stringify(value),headers:{get:()=>null}};
};
window.confirm=(message)=>{window.__confirmText=String(message);return true;};
'''

with sync_playwright() as p:
 b=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
 for width,height in [(320,700),(375,812),(393,852),(430,932)]:
  page=b.new_page(viewport={'width':width,'height':height},is_mobile=True,has_touch=True)
  errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
  page.add_init_script(MOCK)
  page.evaluate(MOCK)
  for url in ('https://fonts.googleapis.com/**','https://fonts.gstatic.com/**','https://telegram.org/**'):
   page.route(url,lambda route:route.abort())
  page.set_content(HTML,wait_until='domcontentloaded')
  page.evaluate('''async()=>await openEventJoinPayment({id:21,title:'Настолки'})''')
  page.wait_for_function("window.__requests.some(x=>x.path==='/api/events/21/receipt')")
  reqs=page.evaluate('window.__requests')
  click=[i for i,r in enumerate(reqs) if r['path']=='/api/events/21/join-intent' and r['method']=='POST']
  receipt=[i for i,r in enumerate(reqs) if r['path']=='/api/events/21/receipt']
  assert click and receipt and click[0] < receipt[0],(width,reqs)
  assert page.locator('#eventCheckinContent').is_visible()
  page.evaluate('''async()=>await openAdminEventReceipts({id:21,title:'Настолки'})''')
  page.wait_for_function("document.querySelector('#eventCheckinContent')?.textContent.includes('Натиснув')")
  text=page.locator('#eventCheckinContent').inner_text()
  assert '14:35' in text and '14:42' in text,(width,text)
  assert 'Не зафіксовано' in text,(width,text)
  assert 'Рішення адміністратора' in text,(width,text)
  assert page.locator('[data-review="approved"]').count()==1
  assert page.locator('.event-receipt-timeline').count()==2
  page.locator('[data-review="approved"]').click()
  page.wait_for_function('Boolean(window.__confirmText)')
  confirm=page.evaluate('window.__confirmText')
  assert 'Натиснув «Я йду»: 22.11.2026, 14:35' in confirm,(width,confirm)
  assert 'Квитанцію отримано: 22.11.2026, 14:42' in confirm,(width,confirm)
  if width==393:
   page.screenshot(path=str(ROOT/'preview_admin_receipt_timeline_393.png'))
  assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),width
  assert not errors,(width,errors)
  print('PASS',width,'first click API call, Kyiv times, legacy fallback, pending and reviewed timeline')
  page.close()
 b.close()
print('ALL JOIN TIMELINE UI TESTS PASSED')

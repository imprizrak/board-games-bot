"""Browser regression: event description external URL gets a safe short link."""
from pathlib import Path
from playwright.sync_api import sync_playwright

root=Path(__file__).resolve().parent
html=(root/'index.html').read_text('utf-8')
mock=r'''
window.__opened=[];
window.__apiRequests=[];
window.Telegram={WebApp:{
 initData:'query_id=demo&user=%7B%22id%22%3A123%7D',
 initDataUnsafe:{user:{id:123,username:'test_member',first_name:'Тест'}},
 ready(){},expand(){},onEvent(){},showAlert(m){window.__alert=m;},
 openLink(url,options){window.__opened.push({url,options})},
 setHeaderColor(){},setBackgroundColor(){},setBottomBarColor(){},
 HapticFeedback:{notificationOccurred(){},impactOccurred(){}},
 isExpanded:true,safeAreaInset:{top:0,bottom:0},contentSafeAreaInset:{top:0,bottom:0}
}};
window.fetch=async(url,options={})=>{
 const path=String(url);window.__apiRequests.push(path);
 let data=[];
 if(path.includes('/api/admins/check'))data={is_admin:false};
 else if(path.includes('/api/events'))data=[];
 else if(path.includes('/api/games'))data=[];
 else if(path.includes('/api/admin'))data={is_admin:false};
 else if(path.includes('/api/event-submissions?scope=pending'))data=[{id:9,title:'Чаювання',event_date:'2026-12-30',event_time:'18:30',status:'pending',submitted_by_display_name:'Гість',price_kop:0,description:'Тут: https://example.com/p/1?x=2&z=3'}];
 return {ok:true,status:200,json:async()=>data,text:async()=>JSON.stringify(data),headers:{get:()=>null}};
};
'''

def run(page,width):
  errors=[]
  page.on('pageerror',lambda e:errors.append(str(e)))
  page.add_init_script(mock)
  page.evaluate(mock)
  for host in ['https://fonts.googleapis.com/**','https://fonts.gstatic.com/**','https://telegram.org/**']:
    page.route(host,lambda r:r.abort())
  page.set_content(html,wait_until='domcontentloaded')
  desc='Реєстрація через Instagram: https://www.instagram.com/guanyin_cha?dlrf=MWwhZGh1YWNsODc1Yw==.\nДодатково (https://example.com/hi?q=hello&msg=1).'
  event={"id":987123,"title":"Чайна дегустація","event_date":"2026-12-30","event_time":"19:00","description":desc,"status":"scheduled","price_kop":0,"rsvps":[],"attendance_reward_stils":0}
  page.evaluate('''ev=>{isAdmin=false;eventsMode='upcoming';neonEventKind='all';document.getElementById('eventSearch').value='';renderEvents([ev]);}''',event)
  anchors=page.locator('.event-card[data-event-id="987123"] .ev-description-link')
  assert anchors.count()==2, (width,anchors.count())
  assert all('Посилання' in t for t in anchors.all_inner_texts())
  body=page.locator('.event-card[data-event-id="987123"] .ev-description').inner_text()
  assert 'https://' not in body
  assert 'Реєстрація через Instagram:' in body
  first_href=anchors.nth(0).get_attribute('href')
  second_href=anchors.nth(1).get_attribute('href')
  assert first_href=='https://www.instagram.com/guanyin_cha?dlrf=MWwhZGh1YWNsODc1Yw=='
  assert second_href=='https://example.com/hi?q=hello&msg=1'
  assert anchors.nth(0).get_attribute('target')=='_blank'
  assert 'noopener' in anchors.nth(0).get_attribute('rel')
  # Open link inside Telegram instead of doing a full-page navigation.
  anchors.first.click()
  assert page.evaluate('window.__opened')[-1]['url']==first_href
  assert page.url=='about:blank'
  # Malicious HTML is escaped and unsafe script: URLs are never activated.
  sample='Перевір <img src=x onerror=alert(1)> javascript:alert(1) https://site.example/path?x=1&y=2'
  result=page.evaluate('x=>renderEventDescriptionWithLinks(x)',sample)
  assert '&lt;img ' in result
  assert 'href="javascript:' not in result
  assert 'https://site.example/path?x=1&amp;y=2' in result
  assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth+1')
  assert not [e for e in errors if 'SyntaxError' in e or 'not defined' in e],(width,errors)
  if width==393:
    page.screenshot(path=str(root/'preview_clickable_links_393.png'))
  print('PASS',width,'links replaced with clickable labels, Telegram openLink, safe href, no page overflow')

with sync_playwright() as p:
 b=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
 for w,h in [(320,700),(375,812),(393,852),(430,932)]:
   pg=b.new_page(viewport={'width':w,'height':h},device_scale_factor=1,is_mobile=True,has_touch=True)
   try:run(pg,w)
   finally:pg.close()
 b.close()
print('ALL EVENT LINK TESTS PASSED')

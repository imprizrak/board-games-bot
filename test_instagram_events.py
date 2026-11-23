"""Local UI regression: mobile Instagram-style event feed and no attendance payouts."""
from pathlib import Path
from PIL import Image
from io import BytesIO
import base64
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).parent
html=(ROOT/'index.html').read_text(encoding='utf-8')
app=(ROOT/'app.py').read_text(encoding='utf-8')
assert 'class="neon-event-filters"' not in html
assert 'eventAttendanceReward' not in html
assert '🪙 +${Number(ev.attendance_reward_stils' not in html
assert '"attendance_reward_stils": 0,' in app
assert "reward = 0" in app
assert "payload['attendance_reward_stils'] = 0" in app
assert 'eventAttendanceStatsHtml(ev, going.length)' in html

def picture(path):
 im=Image.open(path).convert('RGB'); im.thumbnail((520,760)); b=BytesIO();im.save(b,format='JPEG',quality=80)
 return 'data:image/jpeg;base64,'+base64.b64encode(b.getvalue()).decode()
photos=[picture(Path('/mnt/data/a_highly_detailed_glossy_neon_3d_cg_poster_in_the.png')),
        picture(Path('/mnt/data/a_highly_detailed_neon_glow_cinematic_poster_sty.png')),
        picture(Path('/mnt/data/a_vivid_polished_neon_glow_poster_in_a_vertical.png'))]
events=[
 {'id':21,'title':'Настолки: вечір нових знайомств','event_date':'2026-11-29','event_time':'17:00','price_kop':10000,'payment_method':'receipt','payment_link':'https://send.monobank.ua/jar/fake', 'cover_url':photos[0],'description':'Збираємось на затишний вечір настільних ігор. Для новачків і досвідчених гравців.','status':'scheduled','rsvps':[],'max_participants':12,'attendance_reward_stils':50},
 {'id':19,'title':'Чайна дегустація','event_date':'2026-11-24','event_time':'19:00','price_kop':0,'payment_method':'free','cover_url':photos[1],'description':'Спробуємо кілька сортів чаю, поспілкуємось та відпочинемо.','status':'scheduled','rsvps':[],'max_participants':10},
 {'id':20,'title':'Розфарбовуємо фігурки','event_date':'2026-12-02','event_time':'18:00','price_kop':30000,'payment_method':'receipt','cover_url':photos[2],'description':'Майстер-клас із розпису мініатюр для початківців.','status':'scheduled','rsvps':[],'max_participants':8},
]
MOCK='''window.Telegram={WebApp:{initData:'query_id=test&user=123',initDataUnsafe:{user:{id:123,first_name:'Tester'}},platform:'ios',version:'8.0',isFullscreen:false,
 safeAreaInset:{top:54,bottom:20},contentSafeAreaInset:{top:110,bottom:22},
 isVersionAtLeast(v){return true},onEvent(){},ready(){},expand(){},requestFullscreen(){this.isFullscreen=true},
 setHeaderColor(){},setBackgroundColor(){},setBottomBarColor(){},HapticFeedback:{selectionChanged(){},impactOccurred(){},notificationOccurred(){}},
 showAlert(){},showConfirm(s,cb){cb(false)},MainButton:{show(){},hide(){},setText(){},onClick(){},offClick(){},enable(){},disable(){}}}};
window.fetch=async()=>({ok:true,status:200,json:async()=>[],text:async()=>'[]',headers:{get(){return null}}});'''
with sync_playwright() as p:
 browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
 for w,h in [(320,700),(375,812),(393,852),(430,932),(768,1024)]:
  page=browser.new_page(viewport={'width':w,'height':h},is_mobile=True,has_touch=True,device_scale_factor=1)
  page.add_init_script(MOCK)
  for domain in ['https://fonts.googleapis.com/**','https://fonts.gstatic.com/**','https://telegram.org/**']:
   page.route(domain,lambda r:r.abort())
  errors=[]
  page.on('pageerror',lambda e:errors.append(str(e)))
  page.set_content(html,wait_until='domcontentloaded')
  page.evaluate('events => { lastEventsData=events; renderEvents(events); }',events)
  assert page.locator('#view-events').is_visible(),w
  assert page.locator('#view-events .neon-event-filters').count()==0,w
  assert page.locator('.event-card').count()==3,w
  assert page.locator('.event-card .ev-reward').count()==0,w
  assert page.locator('.event-card .ev-post-media').count()==3,w
  assert page.locator('.event-card .event-cover').count()==3,w
  assert page.locator('.event-card .ev-rsvp-btn').count()==3,w
  assert page.locator('#eventSearch').count()==1,w
  box=page.locator('.event-card .ev-post-media').first.bounding_box()
  assert box and box['width']>=w*0.85 and box['height']>=box['width']*.68,(w,box)
  dimensions=page.evaluate('''()=>({screen:window.innerWidth,doc:document.documentElement.scrollWidth,
     img:document.querySelector('.ev-post-media img').getBoundingClientRect().width,
     meta:document.querySelector('.ev-post-info').getBoundingClientRect().width})''')
  assert dimensions['doc']<=dimensions['screen']+2,(w,dimensions)
  page.locator('#eventSearch').fill('чай')
  assert page.locator('.event-card').count()==1,(w,'filter count')
  page.locator('#eventSearch').fill('')
  assert page.locator('.event-card').count()==3,w
  if w==393:
   # First post in view; a second shot demonstrates vertical feed when scrolled.
   page.screenshot(path=str(ROOT/'preview_events_post_393.png'))
   page.locator('.event-card .ev-post-info').first.scroll_into_view_if_needed()
   page.locator('.event-card').first.screenshot(path=str(ROOT/'preview_events_post_details_393.png'))
  if w==320:page.screenshot(path=str(ROOT/'preview_events_post_320.png'))
  assert not errors,(w,errors)
  page.close()
 browser.close()
print('PASS: Instagram-style cover+metadata, search, buttons, no reward UI and no overflow on five viewport widths')

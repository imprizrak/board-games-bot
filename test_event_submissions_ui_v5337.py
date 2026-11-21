"""Playwright smoke tests for participant proposals and admin inbox."""
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).parent
HTML=(ROOT/'index.html').read_text('utf-8')
PAGE_MOCK=r'''
window.__apiRequests=[];
window.__admin=false;
window.Telegram={WebApp:{
  initData:'query_id=demo&user=%7B%22id%22%3A123%7D',
  initDataUnsafe:{user:{id:123,username:'test_member',first_name:'Тест'}},
  ready(){},expand(){},showAlert(message){window.__lastAlert=message},
  onEvent(){},setHeaderColor(){},setBackgroundColor(){},setBottomBarColor(){},
  HapticFeedback:{notificationOccurred(){},impactOccurred(){}},
  isExpanded:true,safeAreaInset:{top:0,bottom:0},contentSafeAreaInset:{top:0,bottom:0}
}};
window.confirm=()=>true;
window.prompt=()=>'';
window.fetch=async(url,options={})=>{
 const path=String(url);
 window.__apiRequests.push({path,method:options.method||'GET'});
 let data=[];let status=200;
 if(path.includes('/api/admins/check')) data={is_admin:window.__admin};
 else if(path.includes('/api/event-submissions?scope=mine'))data=[{id:7,title:'Моя зустріч',event_date:'2026-12-25',status:'pending'}];
 else if(path.includes('/api/event-submissions?scope=pending'))data=[{id:9,title:'Гра і чай',event_date:'2026-12-30',event_time:'18:30',status:'pending',submitted_by_display_name:'Гість',price_kop:15000,location_text:'Приватна адреса',payment_method:'receipt',payment_link:'https://send.monobank.ua/jar/Abcd1234',payment_details:'Квитанція',game_names:['Coup'],description:'Зустріч'}];
 else if(path==='/api/event-submissions' && options.method==='POST') {data={status:'pending',submission_id:7};status=201;}
 else if(path==='/api/event-submissions/9' && options.method==='PATCH')data={status:'updated'};
 else if(path==='/api/event-submissions/9/review')data={status:'approved',event_id:42};
 else if(path.includes('/api/events'))data=[];
 else if(path.includes('/api/games'))data=[];
 else if(path.includes('/api/admin'))data={is_admin:window.__admin};
 return {ok:status<400,status,json:async()=>data,text:async()=>JSON.stringify(data),headers:{get:()=>null}};
};
'''

def test(page,width,height):
    errors=[]
    page.on('pageerror',lambda e:errors.append(str(e)))
    page.add_init_script(PAGE_MOCK)
    page.evaluate(PAGE_MOCK)
    page.route('https://fonts.googleapis.com/**',lambda r:r.abort())
    page.route('https://fonts.gstatic.com/**',lambda r:r.abort())
    page.route('https://telegram.org/**',lambda r:r.abort())
    page.set_content(HTML,wait_until='domcontentloaded')
    page.evaluate("showMainView('view-events');isAdmin=false;applyAdminPermissionsUi()")
    assert page.locator('#eventsFab').is_visible(), 'member + FAB'
    page.locator('#eventsFab').click()
    assert page.locator('#eventModal').is_visible()
    assert page.locator('#eventProposalNotice').is_visible()
    assert 'Запропонувати' in page.locator('#eventModalHeading').inner_text()
    # Modal calendar opens above form and selection works.
    page.evaluate("openEventDatePicker()")
    assert page.locator('#eventDatePickerModal').is_visible()
    page.evaluate("closeModal('eventDatePickerModal');setEventDateField('2026-12-30')")
    assert page.locator('#eventDate').input_value()=='2026-12-30'
    page.locator('#eventTitle').fill('Тестова подія учасника')
    page.locator('#eventTime').fill('18:00')
    page.evaluate('submitEvent()')
    page.wait_for_function("window.__apiRequests.some(x=>x.path==='/api/event-submissions' && x.method==='POST')")
    assert not any(x['path']=='/api/events' and x['method']=='POST' for x in page.evaluate('window.__apiRequests'))
    page.wait_for_timeout(100)
    assert page.locator('#eventSubmissionsModal').is_visible()
    assert 'Моя зустріч' in page.locator('#eventSubmissionsContent').inner_text()
    page.evaluate("closeModal('eventSubmissionsModal')")
    if width==393:
        page.screenshot(path=str(ROOT/'preview_member_proposal_393.png'))
    page.evaluate('window.__admin=true;isAdmin=true;applyAdminPermissionsUi()')
    assert page.locator('#eventPendingReviewButton').is_visible()
    page.locator('#eventPendingReviewButton').click()
    page.wait_for_function("document.querySelector('#eventSubmissionsContent')?.textContent.includes('Приватна адреса')")
    assert page.locator('[data-action="approve"]').is_visible()
    assert 'Квитанція' in page.locator('#eventSubmissionsContent').inner_text()
    if width==393:
        page.screenshot(path=str(ROOT/'preview_admin_moderation_393.png'))
    page.locator('[data-action="edit"]').click()
    assert page.locator('#eventModal').is_visible()
    assert 'Редагувати заявку' in page.locator('#eventModalHeading').inner_text()
    page.locator('#eventTitle').fill('Нова назва чаювання')
    page.evaluate('submitEvent()')
    page.wait_for_function("window.__apiRequests.some(x=>x.path==='/api/event-submissions/9' && x.method==='PATCH')")
    page.wait_for_timeout(60)
    assert page.locator('#eventSubmissionsModal').is_visible()
    page.locator('[data-action="approve"]').click()
    page.wait_for_function("window.__apiRequests.some(x=>x.path==='/api/event-submissions/9/review' && x.method==='POST')")
    page.wait_for_timeout(100)
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    bad=[e for e in errors if any(s in e for s in ('is not defined','Unexpected token','Cannot read properties'))]
    assert not bad,(width,bad[:10])
    print('PASS',width,height,'member proposal, calendar, mine, private admin review, edit, approve', 'page errors:',len(errors))

with sync_playwright() as p:
    b=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    for width,height in [(320,700),(375,812),(393,852),(430,932)]:
        page=b.new_page(viewport={'width':width,'height':height},device_scale_factor=1,is_mobile=True,has_touch=True)
        try:test(page,width,height)
        finally:page.close()
    b.close()
print('ALL PROPOSAL UI BROWSER TESTS PASSED')

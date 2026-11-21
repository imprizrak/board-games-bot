from playwright.sync_api import sync_playwright
from pathlib import Path
from http.server import ThreadingHTTPServer,SimpleHTTPRequestHandler
from functools import partial
from threading import Thread
import json, time

ROOT = Path(__file__).parent.parent
SAMPLE_EVENTS = [
 {'id':10001,'title':'Настільний вечір','event_date':'2026-12-06','event_time':'18:00','description':'Ігри для компанії та новачків. Обираємо настолки разом.', 'cover_url':'','price_kop':15000,'payment_method':'receipt','max_participants':12,'status':'scheduled','rsvps':[],'attendance_reward_stils':50},
 {'id':10002,'title':'Чаювання та дегустація','event_date':'2026-12-07','event_time':'16:00','description':'Спокійна зустріч із чаєм та спілкуванням.','cover_url':'','price_kop':10000,'payment_method':'receipt','max_participants':10,'status':'scheduled','rsvps':[], 'attendance_reward_stils':50},
 {'id':10003,'title':'Розмальовування мініатюр','event_date':'2026-12-11','event_time':'14:00','description':'Фігурки, фарби, творчість і натхнення.', 'cover_url':'','price_kop':20000,'payment_method':'receipt','max_participants':8,'status':'scheduled','rsvps':[],'attendance_reward_stils':50}
]
SAMPLE_GAMES=[
 {'id':1,'name':'Coup','tags':'3–6 гравців, 20 хв. Party','description':'Гра на блеф','cover_url':'','is_favorite':False},
 {'id':2,'name':'Spicy','tags':'2–6 гравців, 15–20 хв, Карткова','description':'Карткова гра','cover_url':'','is_favorite':False},
 {'id':3,'name':'Листи закоханих','tags':'Карткова','description':'Коротка гра','cover_url':'','is_favorite':False}
]

server=ThreadingHTTPServer(("127.0.0.1",0),partial(SimpleHTTPRequestHandler,directory=str(ROOT)))
Thread(target=server.serve_forever,daemon=True).start()
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--disable-dev-shm-usage'])
    results=[]
    for width,height in [(320,720),(375,812),(393,852),(430,932),(768,1024)]:
        page=browser.new_page(viewport={'width':width,'height':height},device_scale_factor=1)
        errs=[]
        page.on('pageerror',lambda e: errs.append(str(e)))
        page.route('https://**/*',lambda route:route.abort())
        page.set_content(ROOT.joinpath("index.html").read_text(encoding="utf-8"),wait_until="domcontentloaded",timeout=25000)
        page.wait_for_timeout(600)
        state=page.evaluate('''({renderEventsType:typeof renderEvents,renderGamesType:typeof renderGamesGrid,nav:[...document.querySelectorAll('.bottom-nav .nav-btn')].map(b=>b.dataset.view)})''')
        assert state['renderEventsType']=='function',state
        assert state['renderGamesType']=='function',state
        page.evaluate('''(events) => {lastEventsData = events; eventsMode="upcoming";renderEvents(events)}''',SAMPLE_EVENTS)
        page.screenshot(path=str(ROOT/f'preview_events_{width}.png'),full_page=False)
        ev_count=page.locator('.event-card').count()
        assert ev_count==3,(ev_count,width)
        category=page.locator('[data-event-kind="tea"]')
        category.click(); assert page.locator('.event-card').count()==1,width
        assert 'Чаювання' in page.locator('.event-card .ev-title').inner_text(),width
        page.locator('[data-event-kind="all"]').click(); assert page.locator('.event-card').count()==3
        field=page.locator('#eventSearch')
        field.fill('мініатюр')
        assert page.locator('.event-card').count()==1,width
        field.fill('');assert page.locator('.event-card').count()==3
        # Check switching stays functional
        page.locator('.bottom-nav [data-view="view-library"]').click()
        page.evaluate('''(games) => {allGames=games;renderGamesGrid()}''',SAMPLE_GAMES)
        assert page.locator('#gamesGrid .card').count()==3
        page.screenshot(path=str(ROOT/f'preview_library_{width}.png'),full_page=False)
        page.locator('.bottom-nav [data-view="view-tools"]').click()
        assert page.locator('#view-tools').evaluate('(el) => el.classList.contains("active")')
        page.screenshot(path=str(ROOT/f'preview_tools_{width}.png'),full_page=False)
        page.locator('.bottom-nav [data-view="view-events"]').click()
        page.evaluate('''() => {lastEventsData = [];renderEvents([])}''')
        assert 'На цей час пакет пустий' in page.locator('.events-empty').inner_text()
        page.screenshot(path=str(ROOT/f'preview_empty_{width}.png'),full_page=False)
        overflow=page.evaluate('''() => ({width:document.documentElement.scrollWidth,client:document.documentElement.clientWidth,
          eventsWidth:document.getElementById('view-events').scrollWidth})''')
        assert overflow['width'] <= overflow['client']+1,(width,overflow)
        results.append((width,ev_count,overflow,errs[:2]))
        page.close()
    for x in results: print(x)
    browser.close()

server.shutdown()

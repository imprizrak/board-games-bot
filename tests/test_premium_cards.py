from pathlib import Path
from playwright.sync_api import sync_playwright
import base64

ROOT=Path(__file__).resolve().parent.parent
SRC=ROOT/'index.html'

def cover_fixture(name):
    data = (ROOT/'tests'/'fixtures'/name).read_bytes()
    return 'data:image/jpeg;base64,'+base64.b64encode(data).decode()

GAMES=[
 {'id':9001,'name':'Coup','tags':'3–6 гравців, 20 хв. Party','cover_url':cover_fixture('coup.jpg'),'is_favorite':False},
 {'id':9002,'name':'Spicy','tags':'2–6 гравців, 15–20 хв, Карткова','cover_url':cover_fixture('spicy.jpg'),'is_favorite':False},
 {'id':9003,'name':'Листи закоханих','tags':'2–4 гравці, 15 хв, Карткова','cover_url':'','is_favorite':False},
 {'id':9004,'name':'Дуже довга назва гри, яка повинна переноситися та не виходити за межі маленької картки','tags':'Кооператив','cover_url':'','is_favorite':False},
 {'id':9005,'name':'Випадкова гра','tags':'','cover_url':'invalid://image','is_favorite':False},
]

with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox','--disable-dev-shm-usage'])
    for width,height in [(320,720),(375,812),(393,852),(430,932),(768,1024)]:
        page=browser.new_page(viewport={'width':width,'height':height},device_scale_factor=1)
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.route('https://**/*', lambda route: route.abort())
        page.set_content(SRC.read_text('utf-8'),wait_until='domcontentloaded',timeout=30000)
        page.evaluate('''(games) => { isAdmin = true; allGames = games; showMainView('view-library'); renderGamesGrid(); }''', GAMES)
        page.wait_for_timeout(700)
        assert page.locator('#gamesGrid .card').count()==len(GAMES),(width,page.locator('#gamesGrid .card').count())
        assert '3–6' in page.locator('#gamesGrid .card:first-child .library-card-stats').inner_text()
        assert '20 хв' in page.locator('#gamesGrid .card:first-child .library-card-stats').inner_text()
        assert 'Party' in page.locator('#gamesGrid .card:first-child .library-card-category').inner_text()
        assert 'Карткова' in page.locator('#gamesGrid .card:nth-child(2) .library-card-category').inner_text()
        assert page.locator('#gamesGrid .card:nth-child(4) .library-card-stat').count()==0
        assert page.locator('#gamesGrid .card:nth-child(5) .library-card-box[hidden]').count()==1
        page.screenshot(path=str(ROOT/f'premium_library_{width}.png'))
        bounds=page.evaluate('''() => ({doc:document.documentElement.scrollWidth,viewport:document.documentElement.clientWidth,
          cards:[...document.querySelectorAll('#gamesGrid .card')].map(c=>({w:c.clientWidth,sw:c.scrollWidth,x:c.getBoundingClientRect().x,right:c.getBoundingClientRect().right})),
          zipper:getComputedStyle(document.querySelector('#gamesGrid .card'),'::before').height})''')
        assert bounds['doc'] <= bounds['viewport']+1,(width,bounds)
        assert all(b['sw']<=b['w']+1 for b in bounds['cards']), (width,bounds)
        assert all(b['x']>=0 and b['right'] <= width+1 for b in bounds['cards']),(width,bounds)
        # Cards still open details and admin favorites remain in place.
        star=page.locator('#gamesGrid .card:first-child .fav-star')
        star.click()
        assert page.locator('#gamesGrid .card:first-child .fav-star.active').count()==1
        assert page.locator('#viewModal.open').count()==0
        page.locator('#gamesGrid .card:first-child').click()
        assert page.locator('#viewModal.open').count()==1
        page.evaluate("closeModal('viewModal')")
        search=page.locator('#librarySearch')
        search.fill('Spicy');assert page.locator('#gamesGrid .card').count()==1
        search.fill('');assert page.locator('#gamesGrid .card').count()==len(GAMES)
        assert not [e for e in errors if 'SyntaxError' in e or 'ReferenceError' in e],(width,errors)
        print('PASS',width,'cards',len(GAMES),'width',bounds['doc'], 'card',bounds['cards'][0])
        page.close()
    browser.close()

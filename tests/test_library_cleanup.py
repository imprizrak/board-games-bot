"""Offline responsive smoke tests for simplified library layout v5.3.29."""
from pathlib import Path
from playwright.sync_api import sync_playwright

folder=Path(__file__).parent.parent
html=(folder/'index.html').read_text('utf-8')
assert html.count('class="lib-subtab-btn active" data-lib="games"')==0
assert '<div class="lib-subtabs">' not in html
for mode in ('wishlist','history','stats'):
    assert f'class="lib-subtab-btn" data-lib="{mode}"' not in html
assert 'class="library-discovery" aria-label="Ігровий компас"' not in html
assert 'id="gameModerationRow"' not in html
assert 'id="gamesGrid"' in html
assert 'id="librarySearch"' in html
assert 'id="libGenreRow"' in html
assert 'styloteka-zip-bag-theme-v5327' in html

with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    for width in (320,375,393,430,768):
        page=browser.new_page(viewport={'width':width,'height':760},device_scale_factor=1)
        page.route('https://telegram.org/**',lambda route:route.abort())
        page.route('https://fonts.googleapis.com/**',lambda route:route.abort())
        page.route('https://fonts.gstatic.com/**',lambda route:route.abort())
        page.route('**/api/**',lambda route:route.fulfill(status=200,content_type='application/json',body='[]'))
        errors=[]
        page.on('pageerror',lambda ex:errors.append(str(ex)))
        page.set_content(html,wait_until='domcontentloaded')
        page.locator('.nav-btn[data-view="view-library"]').click()
        assert page.locator('#view-library .lib-subtabs').count()==0
        assert page.locator('#view-library .lib-subtab-btn').count()==0
        assert page.locator('.library-discovery').count()==0
        assert page.locator('#gameModerationRow').count()==0
        assert page.locator('#librarySearch').is_visible()
        assert page.locator('#libGenreRow').is_visible()
        assert page.locator('#gamesGrid').is_visible()
        page.evaluate('''() => {
          allGames=[
            {id:501,name:'Кольт Експрес',tags:'Стратегія · 2–6 гравців',cover_url:''},
            {id:502,name:'Дуже довга назва настільної гри для перевірки перенесення',tags:'Карткова · 20–40 хв',cover_url:''},
            {id:503,name:'Евердел',tags:'Сімейна · стратегія',cover_url:''},
            {id:504,name:'Кооперативна пригода',tags:'Кооператив',cover_url:''}
          ];
          renderGamesGrid();
        }''')
        assert page.locator('#gamesGrid .card').count()==4
        assert page.locator('#gamesGrid .library-card-cover').count()==4
        assert page.locator('#gamesGrid .card-title').count()==4
        assert page.locator('.bottom-nav .nav-btn').count()==3
        # Legacy deep-link routes resolve to library rather than a hidden panel.
        page.evaluate("openLibraryMode('history')")
        assert page.locator('#libmode-games.active').count()==1
        assert not page.locator('#libmode-history').is_visible()
        assert not page.locator('#libmode-wishlist').is_visible()
        # Basic content fits without horizontal page overflow.
        dims=page.evaluate('''() => ({
          page: document.documentElement.scrollWidth,
          screen: innerWidth,
          cards: [...document.querySelectorAll('#gamesGrid .card')].map(x=>{
             let r=x.getBoundingClientRect();return {l:r.left,r:r.right,w:r.width}
          })
        })''')
        assert dims['page']<=width+2,(width,dims)
        assert all(c['l']>=-1 and c['r']<=width+1 for c in dims['cards']),(width,dims)
        assert not any('is not defined' in err or 'Cannot read properties' in err for err in errors),(width,errors)
        if width in (320,393,430):
            page.screenshot(path=str(folder/f'preview_library_{width}.png'))
        print(f'PASS width={width}: search, filters, zip cards, 3 tabs, old links safe')
        page.close()
    browser.close()
print('ALL LIBRARY CLEANUP TESTS PASSED')

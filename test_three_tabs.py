"""Smoke test for the user-facing three-tab navigation; no network or real user data."""
from pathlib import Path
import re
from playwright.sync_api import sync_playwright

html=Path(__file__).with_name('index.html').read_text(encoding='utf-8')
nav=html.split('<!-- ==================== НИЖНЄ МЕНЮ ==================== -->',1)[1].split('<!-- Бібліотека +:',1)[0]
assert re.findall(r'data-view="(view-[a-z-]+)"',nav)==['view-events','view-library','view-tools']
assert html.count('id="view-events"')==1
assert '<div class="view active" id="view-events">' in html
assert 'id="eventDatePickerModal"' in html
assert 'id="eventsList"' in html and 'id="gamesGrid"' in html and 'id="panel-dice"' in html
assert 'attendance_reward_stils' in html  # preserved work-in-progress reward UI
assert 'eventRepeatHint' in html

with sync_playwright() as play:
    browser=play.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
    for width in (320,375,430,768):
        page=browser.new_page(viewport={'width':width,'height':740},device_scale_factor=1)
        page.route('https://telegram.org/**', lambda route:route.abort())
        page.route('https://fonts.googleapis.com/**', lambda route:route.abort())
        page.route('https://fonts.gstatic.com/**', lambda route:route.abort())
        page.route('**/api/**', lambda route:route.fulfill(status=200,content_type='application/json',body='[]'))
        errors=[]
        page.on('pageerror',lambda exc: errors.append(str(exc)))
        page.set_content(html, wait_until='domcontentloaded')
        page.wait_for_timeout(500)
        navpage=page.locator('.bottom-nav')
        assert navpage.locator('.nav-btn').count()==3,(width,'nav count')
        assert page.locator('#view-events').evaluate('(x) => getComputedStyle(x).display')!='none',(width,'events should open initially')
        assert page.locator('#eventsFab').evaluate('(x) => getComputedStyle(x).display') == 'none', (width,'guest fab must be hidden')
        assert page.locator('.view.active').count()==1,(width,'active count initially')
        assert page.locator('button[data-view="view-events"]').get_attribute('aria-current')=='page'
        for target in ('view-library','view-tools','view-events'):
            page.locator(f'.nav-btn[data-view="{target}"]').click()
            assert page.locator(f'#{target}').evaluate('(x)=>getComputedStyle(x).display') != 'none', (width,target,'not visible')
            assert page.locator('.view.active').count()==1,(width,target,'multiple active screens')
            assert page.locator(f'.nav-btn[data-view="{target}"]').get_attribute('aria-current')=='page',(width,target,'aria-current')
        page.evaluate("showMainView('view-profile')")
        assert page.locator('#view-events.active').count()==1,(width,'profile must redirect to events')
        page.evaluate("openToolsHub()")
        assert page.locator('#view-tools.active').count()==1,(width,'tools hub')
        page.evaluate("openToolPanel('counters','flip7')")
        assert page.locator('#panel-counters.active').count()==1,(width,'tools panel')
        assert page.locator('#mode-flip7.active').count()==1,(width,'flip7')
        page.evaluate("showMainView('view-events')")
        assert page.locator('#view-tools.active').count()==0,(width,'switch out of tools')
        nav_overflow=page.locator('.bottom-nav').evaluate('(x)=>x.scrollWidth>x.clientWidth+2')
        assert not nav_overflow,(width,'nav horizontal overflow')
        label_stats=page.locator('.bottom-nav button').evaluate_all('(items)=>items.map(x=>({text:x.innerText,scroll:x.scrollWidth,width:x.clientWidth}))')
        assert all(e['scroll']<=e['width']+2 for e in label_stats),(width,label_stats)
        assert not any('is not defined' in e or 'Cannot read properties' in e for e in errors), (width,errors[:5])
        print('PASS',width,'3 tabs, tools, routing, no nav overflow', 'runtime_errors',len(errors))
        if width==320:
            page.screenshot(path=str(Path(__file__).with_name('preview_320.png')))
        page.close()
    browser.close()
print('ALL TESTS PASS')

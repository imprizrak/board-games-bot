"""Styloteka v5.3.30: dice must not use Telegram profiles or submit achievements."""
from pathlib import Path
from playwright.sync_api import sync_playwright

base=Path(__file__).parent.parent
html=(base/'index.html').read_text('utf-8')
assert 'id="diceProfileSelect"' not in html
assert 'Хто кидає кубик' not in html
assert "'dice_roll'" not in html
assert 'recordProfileToolEvent' not in html
assert 'id="pickerProfiles"' not in html
assert 'id="gamesGrid"' in html
assert 'styloteka-zip-bag-theme-v5327' in html
assert 'id="panel-counters"' in html

with sync_playwright() as p:
    browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    for width in (320,375,393,430,768):
        page=browser.new_page(viewport={'width':width,'height':760},device_scale_factor=1)
        page.route('https://telegram.org/**', lambda route:route.abort())
        page.route('https://fonts.googleapis.com/**', lambda route:route.abort())
        page.route('https://fonts.gstatic.com/**', lambda route:route.abort())
        calls=[]
        def intercept(route):
            calls.append((route.request.method, route.request.url))
            route.fulfill(status=200,content_type='application/json',body='[]')
        page.route('**/api/**',intercept)
        errors=[]
        page.on('pageerror',lambda err:errors.append(str(err)))
        page.set_content(html,wait_until='domcontentloaded')
        page.evaluate("openToolPanel('dice')")
        assert page.locator('#panel-dice.active').is_visible(), width
        assert page.locator('#diceProfileSelect').count()==0, width
        assert page.locator('#panel-dice .die-btn').count()==7, width
        assert not page.locator('#panel-dice').get_by_text('Хто кидає кубик').count(), width
        assert page.locator('#diceResult').is_visible(), width
        page.locator('#panel-dice .die-btn[data-sides="20"]').click()
        page.locator('#roll-area').click()
        page.wait_for_timeout(850)
        value=int(page.locator('#diceResult').inner_text())
        assert 1 <= value <= 20,(width,value)
        assert f'd20: {value}' in page.locator('#diceHistory').inner_text(), width
        page.locator('#roll-area').click()
        page.wait_for_timeout(850)
        assert len(page.locator('#diceHistory').inner_text().split('•')) == 2,width
        # No background requests to load other players, nor achievement posting.
        assert not [u for _,u in calls if ('/api/profiles/public' in u or '/api/profile/tool-event' in u)], (width,calls)
        assert not any('is not defined' in err or 'Cannot read properties' in err for err in errors), (width,errors)
        page.evaluate("openToolPanel('picker')")
        assert page.locator('#panel-picker.active').is_visible(), width
        page.evaluate("openToolPanel('counters')")
        assert page.locator('#panel-counters.active').is_visible(), width
        if width==393:
            page.evaluate("openToolPanel('dice')")
            page.screenshot(path=str(base/'preview_dice_393.png'),full_page=False)
        print('PASS',width,'dice roll, history, no profiles, picker/counters intact')
        page.close()
    browser.close()
print('ALL DICE TESTS PASSED')

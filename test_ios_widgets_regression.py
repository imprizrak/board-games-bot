"""Styloteka 5.3.36 widget layout & functional browser test."""
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
html = (ROOT/'index.html').read_text('utf-8')
assert 'styloteka-ios-widgets-v5336' in html
assert 'id="toolWidgetHub"' in html
assert 'id="panel-picker"' in html
assert 'id="panel-dice"' in html
assert 'id="panel-counters"' in html
assert 'id="eventDatePickerModal"' in html
assert 'id="gamesGrid"' in html

with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    for w,h in [(320,700),(375,812),(393,852),(430,932),(768,1024)]:
        page=browser.new_page(viewport={'width':w,'height':h},is_mobile=True,has_touch=True,device_scale_factor=1)
        errors=[]
        page.on('pageerror',lambda e: errors.append(str(e)))
        page.route('https://telegram.org/**',lambda r:r.abort())
        page.route('https://fonts.googleapis.com/**',lambda r:r.abort())
        page.route('https://fonts.gstatic.com/**',lambda r:r.abort())
        page.route('**/api/**',lambda r:r.fulfill(status=200,content_type='application/json',body='[]'))
        page.set_content(html,wait_until='domcontentloaded')
        page.locator('.nav-btn[data-view="view-tools"]').click()
        assert page.locator('#toolWidgetHub').is_visible(),w
        assert page.locator('.ios-widget').count()==4,w
        assert not page.locator('#toolWidgetDetailBar').is_visible(),w
        assert not page.locator('#panel-dice').is_visible(),w
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'),w
        box=page.locator('#toolWidgetHub').bounding_box()
        assert box['x']>=0 and box['x']+box['width']<=w+1,(w,box)
        card_boxes=page.locator('.ios-widget').evaluate_all('els=>els.map(e=>{const r=e.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width,height:r.height}})')
        for b in card_boxes:
            assert b['width']>125 and b['left']>=0 and b['right']<=w+1,(w,b)
        if w==393:
            page.screenshot(path=str(ROOT/'preview_widgets_393.png'),full_page=False)
        if w==320:
            page.screenshot(path=str(ROOT/'preview_widgets_320.png'),full_page=False)
        if w==430:
            page.screenshot(path=str(ROOT/'preview_widgets_430.png'),full_page=False)
        # Dice opens as a detail, records a result, then returns home.
        page.locator('.ios-widget-dice').click()
        assert page.locator('#panel-dice').is_visible(),w
        assert not page.locator('#toolWidgetHub').is_visible(),w
        page.locator('.die-btn[data-sides="20"]').click()
        page.locator('#roll-area').click()
        page.wait_for_function("/Останній: d20 → \\d+/.test(document.querySelector('#widgetDiceSummary').textContent)",timeout=3000)
        page.locator('.ios-widget-back').click()
        assert page.locator('#toolWidgetHub').is_visible(),w
        assert not page.locator('#panel-dice').is_visible(),w
        # Counters open + return.
        page.locator('.ios-widget-counters').click()
        assert page.locator('#mode-simple.active').is_visible(),w
        assert page.locator('#panel-counters.active').is_visible(),w
        page.locator('.ios-widget-back').click()
        # Flip Seven same panel, different mode.
        page.locator('.ios-widget-flip7').click()
        assert page.locator('#mode-flip7.active').is_visible(),w
        assert page.locator('#panel-counters.active').is_visible(),w
        page.locator('.ios-widget-back').click()
        # Picker overlay must fit the WebView and close to widgets, no native fullscreen.
        page.locator('.ios-widget-picker').click()
        d=page.evaluate('''() => {const r=document.querySelector('#panel-picker').getBoundingClientRect();return {l:r.left,r:r.right,h:r.height,parent:document.querySelector('#panel-picker').parentElement.tagName,full:document.body.classList.contains('picker-fullscreen'),nav:getComputedStyle(document.querySelector('.bottom-nav')).display}}''')
        assert d['full'] and d['parent']=='BODY' and d['nav']=='none' and abs(d['h']-h)<=1,(w,d)
        page.locator('.picker-exit').click()
        assert page.locator('#toolWidgetHub').is_visible(),w
        assert not page.locator('#toolWidgetDetailBar').is_visible(),w
        assert not page.locator('#panel-picker').is_visible(),w
        assert page.locator('.bottom-nav').is_visible(),w
        page.locator('.nav-btn[data-view="view-library"]').click()
        assert page.locator('#view-library.active').is_visible(),w
        page.locator('.nav-btn[data-view="view-tools"]').click()
        assert page.locator('#toolWidgetHub').is_visible(),w
        exceptions=[x for x in errors if 'is not defined' in x or 'Cannot read properties' in x or 'Unexpected token' in x]
        assert not exceptions,(w,exceptions[:5])
        print('PASS',w,h,'widget grid, dice, counters, flip7, picker overlay, nav; errors',len(errors))
        page.close()
    browser.close()
print('ALL IOS WIDGET TESTS PASSED')

"""Offline smoke and interaction tests for the profile-free touch picker."""
from pathlib import Path
import re
from playwright.sync_api import sync_playwright

base=Path(__file__).parent.parent
html=(base/'index.html').read_text(encoding='utf-8')
assert 'id="pickerProfiles"' not in html
assert 'renderPickerProfiles' not in html
assert 'nextPickerProfileForTouch' not in html
assert 'selectedPickerProfiles' not in html
assert "recordProfileToolEvent('first_player_win'" not in html
assert 'id="diceProfileSelect"' not in html
assert 'id="gamesGrid"' in html
assert 'styloteka-zip-bag-theme-v5327' in html

with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    for width in [320,393,430,768]:
        page=browser.new_page(viewport={'width':width,'height':760},device_scale_factor=1)
        page.route('https://telegram.org/**',lambda r:r.abort())
        page.route('https://fonts.googleapis.com/**',lambda r:r.abort())
        page.route('https://fonts.gstatic.com/**',lambda r:r.abort())
        tool_post=[]
        def mock_api(route):
            if '/api/profile/tool-event' in route.request.url:
                tool_post.append(route.request.url)
            route.fulfill(status=200,content_type='application/json',body='[]')
        page.route('**/api/**',mock_api)
        errors=[]
        page.on('pageerror',lambda exc:errors.append(str(exc)))
        page.set_content(html,wait_until='domcontentloaded')
        page.evaluate("openToolPanel('picker')")
        picker=page.locator('#panel-picker')
        assert picker.is_visible(),width
        assert picker.locator('#pickerProfiles').count()==0,width
        assert picker.locator('input[type=checkbox]').count()==0,width
        assert 'профілі та імена не потрібні' in picker.inner_text(),width
        touches=page.evaluate('''() => {
          const area=document.getElementById('pickerArea');
          const r=area.getBoundingClientRect();
          const ev=new Event('touchstart',{bubbles:true,cancelable:true});
          Object.defineProperty(ev,'changedTouches',{value:[
            {identifier:101,clientX:r.left+80,clientY:r.top+100},
            {identifier:102,clientX:r.left+180,clientY:r.top+100}
          ]});
          area.dispatchEvent(ev);
          return {number:Object.keys(activeTouches).length,
            fields:Object.values(activeTouches).map(x=>Object.keys(x)),
            dots:area.querySelectorAll('.touch-dot').length,
            labels:area.querySelectorAll('.touch-label').length};
        }''')
        assert touches['number']==2 and touches['dots']==2,(width,touches)
        assert touches['labels']==0,(width,touches)
        assert all('telegramUserId' not in fields and 'profileName' not in fields for fields in touches['fields']),(width,touches)
        page.evaluate("finishPick('101')")
        assert picker.locator('.touch-dot.winner').count()==1,width
        assert picker.locator('.touch-label').count()==0,width
        assert not tool_post,(width,tool_post)
        if width==393:
            page.screenshot(path=str(base/'preview_picker_393.png'))
        page.evaluate('resetPicker()')
        assert picker.locator('.touch-dot').count()==0,width
        assert 'Cannot read properties' not in str(errors), (width,errors)
        page.evaluate("openToolPanel('dice')")
        assert page.locator('#panel-dice.active').count()==1,width
        assert page.locator('#diceProfileSelect').count()==0,width
        assert page.locator('#panel-dice .die-btn').count()==7,width
        print('PASS',width,'profile-free picker, multitouch, winner, reset, dice retained', 'runtime_errors',len(errors))
        page.close()
    browser.close()
print('ALL PICKER TESTS PASSED')

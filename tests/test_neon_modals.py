from pathlib import Path
from playwright.sync_api import sync_playwright
root=Path(__file__).parent.parent
html=(root/'index.html').read_text('utf-8')
with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    for width in (320,393,430):
        p=browser.new_page(viewport={'width':width,'height':820})
        errors=[]
        p.on('pageerror',lambda e:errors.append(str(e)))
        p.route('https://**/*',lambda r:r.abort())
        p.set_content(html,wait_until='domcontentloaded')
        p.evaluate('openEventModal()')
        assert p.locator('#eventModal.open').count()==1
        assert p.locator('#eventTitle').is_visible()
        assert p.locator('#eventPriceMode').is_visible()
        p.evaluate('openEventDatePicker()')
        assert p.locator('#eventDatePickerModal.open').count()==1
        stack=p.evaluate('''() => ({calendar:parseInt(getComputedStyle(document.getElementById('eventDatePickerModal')).zIndex),
        event:parseInt(getComputedStyle(document.getElementById('eventModal')).zIndex)})''')
        assert stack['calendar'] > stack['event'],(width,stack)
        p.locator('#eventDatePickerModal').screenshot(path=str(root/f'preview_calendar_{width}.png'))
        p.evaluate("closeModal('eventDatePickerModal')")
        p.evaluate("closeModal('eventModal')")
        assert not [e for e in errors if 'is not defined' in e or 'Cannot read properties' in e],errors
        p.close()
        print('PASS',width,'form/calendar overlay',stack)
    browser.close()

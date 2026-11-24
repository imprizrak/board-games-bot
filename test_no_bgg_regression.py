from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
html = (ROOT/'index.html').read_text(encoding='utf-8')
app = (ROOT/'app.py').read_text(encoding='utf-8')
for needle in ['BGG_API_TOKEN','BoardGameGeek','/api/games/autofill','gameAutofill','cover_source_url']:
    assert needle.lower() not in html.lower(), ('UI has BGG:',needle)
    assert needle.lower() not in app.lower(), ('server has BGG:',needle)
assert 'styloteka-v5347-soft-blur-bottom-nav' in html
assert '<input type="file" id="inputCover"' in html
assert '<input type="file" id="editCover"' in html
assert '<input type="url" id="inputRulesUrl"' in html
assert '<input type="url" id="inputVideoUrl"' in html
assert 'function openGameReferenceSearch' in html

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox','--disable-dev-shm-usage'])
    for width,height in [(320,700),(375,812),(393,852),(430,932),(768,1024)]:
        page = browser.new_page(viewport={'width':width,'height':height}, is_mobile=True,has_touch=True)
        errors=[]
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.route('https://telegram.org/**',lambda r:r.abort())
        page.route('https://fonts.googleapis.com/**',lambda r:r.abort())
        page.route('https://fonts.gstatic.com/**',lambda r:r.abort())
        page.route('**/api/**',lambda r:r.fulfill(status=200, content_type='application/json',body='[]'))
        page.set_content(html,wait_until='domcontentloaded')
        page.wait_for_timeout(120)
        page.evaluate('window.__opened=[]; Telegram.WebApp.openLink=(url)=>window.__opened.push(url);')
        page.evaluate('openAddModal();')
        assert page.locator('#addModal.open').is_visible()
        assert page.locator('#addModal .game-reference-btn').count()==2
        assert page.locator('#gameAutofillBox').count()==0
        page.locator('#inputName').fill('Каркассон')
        page.locator('#addModal .game-reference-btn').first.click()
        page.locator('#addModal .game-reference-btn').last.click()
        links=page.evaluate('window.__opened')
        assert len(links)>=2 and 'youtube.com/results' in links[-2] and 'google.com/search' in links[-1],links
        assert '%D0%9A' in links[-2], links
        page.evaluate("closeModal('addModal')")
        page.evaluate("isAdmin=true; currentGame={id:77,name:'Каркассон',description:'Гра про міста',tags:'2–5',rules_url:'https://example.org/rules',cover_url:'https://example.org/cover.jpg'}; openEditModal();")
        assert page.locator('#editModal.open').is_visible()
        assert page.locator('#editModal .game-reference-btn').count()==2
        assert page.locator('#editName').input_value()=='Каркассон'
        assert page.locator('#editDescription').input_value()=='Гра про міста'
        assert page.locator('#editRulesUrl').input_value()=='https://example.org/rules'
        assert page.locator('#editGameAutofillBox').count()==0
        page.evaluate("closeModal('editModal')")
        page.locator('.nav-btn[data-view="view-tools"]').click()
        assert page.locator('#toolWidgetHub').is_visible()
        page.locator('.nav-btn[data-view="view-library"]').click()
        assert page.locator('#view-library.active').is_visible()
        page.locator('.nav-btn[data-view="view-events"]').click()
        assert page.locator('#view-events.active').is_visible()
        assert 'blur(8px)' in page.locator('.bottom-nav').evaluate('(el)=>getComputedStyle(el).backdropFilter')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
        assert not [e for e in errors if 'ReferenceError' in e or 'TypeError' in e or 'SyntaxError' in e],errors
        if width==393:
            page.evaluate('openAddModal()')
            page.screenshot(path=str(ROOT/'preview_manual_game_entry_393.png'))
            page.evaluate("closeModal('addModal')")
        print('PASS',width,'manual video/rules search, add/edit, nav, no BGG')
        page.close()
    browser.close()
print('PASS ALL')

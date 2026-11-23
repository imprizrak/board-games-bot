from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
html = (ROOT / 'index.html').read_text(encoding='utf-8')
assert 'styloteka-v5346-mini-clear-bottom-nav' in html
assert 'styloteka-v5347-soft-blur-bottom-nav' in html
assert 'styloteka-autofill-v5345' in html
assert 'styloteka-v5344-post-feed' in html

with sync_playwright() as pw:
    browser = pw.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox', '--disable-dev-shm-usage'])
    for width, height in [(320, 700), (375, 812), (393, 852), (430, 932), (768, 1024)]:
        page = browser.new_page(viewport={'width': width, 'height': height}, is_mobile=True, has_touch=True, device_scale_factor=1)
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.route('https://telegram.org/**', lambda route: route.abort())
        page.route('https://fonts.googleapis.com/**', lambda route: route.abort())
        page.route('https://fonts.gstatic.com/**', lambda route: route.abort())
        page.route('**/api/**', lambda route: route.fulfill(status=200, content_type='application/json', body='[]'))
        page.set_content(html, wait_until='domcontentloaded')
        page.wait_for_timeout(350)
        info = page.evaluate('''() => {
          const n=document.querySelector('.bottom-nav');
          const s=getComputedStyle(n),r=n.getBoundingClientRect();
          const b=n.querySelector('button.active'), t=getComputedStyle(b);
          const buttons=[...n.querySelectorAll('button')].map(x=>{const rr=x.getBoundingClientRect();return {width:rr.width,height:rr.height,left:rr.left,right:rr.right}});
          return {width:r.width,height:r.height,left:r.left,right:r.right,
            radius:s.borderTopLeftRadius,backdrop:s.backdropFilter,webkitBackdrop:s.webkitBackdropFilter,
            background:s.backgroundColor,activeBg:t.backgroundColor,
            labels:[...n.querySelectorAll('button span:last-child')].map(x=>x.textContent.trim()),
            buttons,scrollWidth:document.documentElement.scrollWidth,viewport:innerWidth};
        }''')
        assert info['width'] <= min(310, width-28)+1, (width, info)
        assert info['height'] == 56, (width, info)
        assert info['left'] >= 10 and info['right'] <= width-10, (width, info)
        assert info['radius'] == '999px', (width, info)
        assert 'blur(8px)' in info['backdrop'], (width, info)
        assert info['webkitBackdrop'] is None or 'blur(8px)' in info['webkitBackdrop'], (width, info)
        assert info['background'].startswith('rgba(17, 34, 67, 0.10'), (width, info)
        assert info['activeBg'].startswith('rgba(58, 126, 164, 0.10'), (width, info)
        assert all(b['height'] >= 44 for b in info['buttons']), (width, info)
        assert len(info['labels']) == 3
        assert info['scrollWidth'] <= info['viewport'] + 1, (width, info)
        assert not [e for e in errors if 'Unexpected token' in e or 'Cannot read properties' in e], (width, errors)
        if width in (320, 393, 430):
            page.screenshot(path=str(ROOT / f'preview_soft_blur_nav_{width}.png'))
        page.locator('.nav-btn[data-view="view-library"]').click()
        assert page.locator('#view-library.active').is_visible(), width
        page.locator('.nav-btn[data-view="view-tools"]').click()
        assert page.locator('#toolWidgetHub').is_visible(), width
        page.locator('.nav-btn[data-view="view-events"]').click()
        assert page.locator('#view-events.active').is_visible(), width
        print('PASS', width, 'nav', info['width'], 'x', info['height'], 'soft frosted blur, tabs OK')
        page.close()

    # Accessibility: OS reduced-transparency disables blur and adds readable fill.
    page = browser.new_page(viewport={'width':393,'height':852}, reduced_motion='reduce')
    page.emulate_media(reduced_motion='reduce')
    page.route('**/api/**', lambda route: route.fulfill(status=200, content_type='application/json', body='[]'))
    page.set_content(html, wait_until='domcontentloaded')
    # prefers-reduced-transparency currently not emulatable in Chromium: verify authored CSS statically.
    assert 'prefers-reduced-transparency: reduce' in html
    page.close()
    browser.close()
print('PASS ALL SOFT BLUR NAV TESTS')

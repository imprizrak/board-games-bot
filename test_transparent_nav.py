from pathlib import Path
import json
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).parent
html=(ROOT/'index.html').read_text('utf-8')
assert 'styloteka-v5346-mini-clear-bottom-nav' in html
assert 'styloteka-autofill-v5345' in html
assert 'styloteka-v5344-post-feed' in html

with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    for w,h in [(320,700),(375,812),(393,852),(430,932),(768,1024)]:
        p=browser.new_page(viewport={'width':w,'height':h},is_mobile=True,has_touch=True,device_scale_factor=1)
        errs=[]
        p.on('pageerror',lambda e: errs.append(str(e)))
        p.route('https://telegram.org/**',lambda r:r.abort())
        p.route('https://fonts.googleapis.com/**',lambda r:r.abort())
        p.route('https://fonts.gstatic.com/**',lambda r:r.abort())
        p.route('**/api/**',lambda r:r.fulfill(status=200,content_type='application/json',body='[]'))
        p.set_content(html,wait_until='domcontentloaded')
        p.wait_for_timeout(250)
        info=p.evaluate('''() => {
          const nav=document.querySelector('.bottom-nav');
          const s=getComputedStyle(nav),r=nav.getBoundingClientRect();
          const active=nav.querySelector('button.active'), a=getComputedStyle(active);
          const icons=[...nav.querySelectorAll('button')].map(b=>{
            const x=b.getBoundingClientRect();return {w:x.width,h:x.height,l:x.left,r:x.right,rad:getComputedStyle(b).borderTopLeftRadius}});
          return {width:r.width,height:r.height,left:r.left,right:r.right,bottom:innerHeight-r.bottom,
            radius:s.borderTopLeftRadius,background:s.backgroundColor,backgroundImage:s.backgroundImage,
            backdrop:s.backdropFilter,activeBg:a.backgroundColor,activeImage:a.backgroundImage,
            activeRadius:a.borderTopLeftRadius,labels:[...nav.querySelectorAll('button span:last-child')].map(e=>e.textContent.trim()),
            icons,scroll:document.documentElement.scrollWidth,viewport:innerWidth,
            fabBottom:getComputedStyle(document.querySelector('#eventsFab')).bottom};
        }''')
        assert info['width']<=min(310,w-28)+1, (w,info)
        assert info['height']==56, (w,info)
        assert info['left']>=10 and info['right']<=w-10,(w,info)
        assert info['radius'] in ('999px','999px 999px'),(w,info)
        assert info['background']=='rgba(0, 0, 0, 0)',(w,info)
        assert info['activeBg']=='rgba(0, 0, 0, 0)',(w,info)
        assert info['backgroundImage']=='none' and info['activeImage']=='none',(w,info)
        assert info['backdrop']=='none',(w,info)
        assert all(b['h']>=44 and b['l']>=info['left'] and b['r']<=info['right']+1 for b in info['icons']),(w,info)
        assert info['scroll']<=info['viewport']+1, (w,info)
        assert len(info['labels'])==3,(w,info)
        assert not [e for e in errs if 'Cannot read properties' in e or 'Unexpected token' in e],(w,errs)
        if w in (320,393,430):p.screenshot(path=str(ROOT/f'preview_transparent_nav_{w}.png'))
        # Navigation interaction remains functional.
        p.locator('.nav-btn[data-view="view-library"]').click()
        assert p.locator('#view-library.active').is_visible(),w
        assert p.locator('.nav-btn[data-view="view-library"]').get_attribute('aria-current')=='page'
        p.locator('.nav-btn[data-view="view-tools"]').click()
        assert p.locator('#toolWidgetHub').is_visible(),w
        p.locator('.nav-btn[data-view="view-events"]').click()
        assert p.locator('#view-events.active').is_visible(),w
        print('PASS',w,'nav=',info['width'],info['height'],'transparent, rounded, tappable, tabs')
        p.close()
    browser.close()
print('PASS ALL TRANSPARENT NAV TESTS')

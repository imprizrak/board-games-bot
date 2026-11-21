"""Local smoke test: three-tab compact bottom nav. No external network or production data."""
from pathlib import Path
import re
from playwright.sync_api import sync_playwright

root=Path(__file__).parent.parent
html=(root/'index.html').read_text(encoding='utf-8')
assert html.count('id="styloteka-compact-nav-v5326"')==1
assert 'styloteka-three-tabs-v5325' in html
assert 'id="eventModal"' in html
assert 'id="eventDatePickerModal"' in html
assert 'attendance_reward_stils' in html
assert 'eventRepeatHint' in html
nav=html.split('<!-- ==================== НИЖНЄ МЕНЮ ==================== -->',1)[1].split('<!-- Бібліотека +:',1)[0]
assert re.findall(r'data-view="(view-[a-z-]+)"',nav)==['view-events','view-library','view-tools']

with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    for width in (320,360,375,393,430,768,1024):
        page=browser.new_page(viewport={'width':width,'height':760},device_scale_factor=1)
        page.route('https://telegram.org/**',lambda r:r.abort())
        page.route('https://fonts.googleapis.com/**',lambda r:r.abort())
        page.route('https://fonts.gstatic.com/**',lambda r:r.abort())
        page.route('**/api/**',lambda r:r.fulfill(status=200,content_type='application/json',body='[]'))
        errors=[]
        page.on('pageerror',lambda exc:errors.append(str(exc)))
        page.set_content(html,wait_until='domcontentloaded')
        page.wait_for_timeout(300)
        stats=page.locator('.bottom-nav').evaluate('''el => {
          const r=el.getBoundingClientRect();
          const css=getComputedStyle(el);
          return {left:r.left,right:r.right,width:r.width,height:r.height,
           view:innerWidth, padding:css.padding,display:css.display,
           children:[...el.querySelectorAll('button')].map(b=>{
              let br=b.getBoundingClientRect(),label=b.querySelector('span:last-child');
              return {title:label.textContent.trim(),w:br.width,h:br.height,
               t:label.scrollWidth,c:label.clientWidth,visible:br.left>=0&&br.right<=innerWidth};
           })};
        }''')
        assert stats['width']<=318.1,(width,stats)
        assert stats['width']<=width-39,(width,stats)
        assert abs(stats['left']-(width-stats['right']))<1.5,(width,stats)
        assert 59.5<=stats['height']<=60.5,(width,stats)
        assert len(stats['children'])==3,(width,stats)
        assert all(x['h']>=44 for x in stats['children']),(width,stats)
        assert all(x['visible'] and x['t']<=x['c']+1 for x in stats['children']),(width,stats)
        for target in ('view-library','view-tools','view-events'):
            page.locator(f'.nav-btn[data-view="{target}"]').click()
            assert page.locator(f'#{target}.active').count()==1,(width,target)
            assert page.locator(f'.nav-btn[data-view="{target}"]').get_attribute('aria-current')=='page'
        assert page.locator('#eventsFab').evaluate('(x)=>getComputedStyle(x).display')=='none',(width,'guest fab')
        assert not any('is not defined' in e or 'Cannot read properties' in e for e in errors),errors[:5]
        if width in (320,393,430):
            page.screenshot(path=str(root/f'preview_{width}.png'))
        print('PASS',width,'nav',round(stats['width']), 'x',round(stats['height']), 'button heights', [round(x['h']) for x in stats['children']])
        page.close()
    browser.close()
print('PASS ALL: compact nav, 3-tab routing, touch targets and no overflow')

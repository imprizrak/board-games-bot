"""Regression for perfectly circular concentric live multi-touch markers in Styloteka."""
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT = Path(__file__).parent
html = (ROOT/'index.html').read_text('utf-8')
assert 'styloteka-picker-round-rings-v5343' in html
MOCK = '''
window.Telegram={WebApp:{initData:'query_id=test&user=123',initDataUnsafe:{user:{id:123,first_name:'Test'}},
 platform:'ios',version:'8.0',isFullscreen:false,safeAreaInset:{top:54,bottom:20},contentSafeAreaInset:{top:110,bottom:22},
 isVersionAtLeast(v){return true},onEvent(){},ready(){},expand(){},requestFullscreen(){this.isFullscreen=true},
 setHeaderColor(){},setBackgroundColor(){},setBottomBarColor(){},
 HapticFeedback:{selectionChanged(){},impactOccurred(){},notificationOccurred(){}},showAlert(){},showConfirm(s,cb){cb(true)},
 MainButton:{show(){},hide(){},setText(){},onClick(){},offClick(){},enable(){},disable(){}}}};
window.fetch=async()=>({ok:true,status:200,json:async()=>[],text:async()=>'[]',headers:{get(){return null}}});
'''
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
    for w,h in [(320,700),(375,812),(393,852),(430,932),(768,1024)]:
        page=browser.new_page(viewport={'width':w,'height':h},is_mobile=True,has_touch=True)
        page.add_init_script(MOCK)
        page.route('**/api/**', lambda route: route.fulfill(status=200, content_type='application/json',body='[]'))
        page.route('https://fonts.googleapis.com/**',lambda r:r.abort())
        page.route('https://fonts.gstatic.com/**',lambda r:r.abort())
        page.route('https://telegram.org/**',lambda r:r.abort())
        errors=[]
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.set_content(html,wait_until='domcontentloaded')
        page.locator('.nav-btn[data-view="view-tools"]').click()
        page.locator('.ios-widget-picker').click()
        assert page.locator('#panel-picker').is_visible()
        assert page.locator('body.picker-fullscreen').count()==1
        cdps=page.context.new_cdp_session(page)
        box=page.locator('#pickerArea').bounding_box()
        assert box and box['height']>200,(w,box)
        x1=box['x']+min(90,box['width']*.29)
        x2=box['x']+box['width']-min(90,box['width']*.29)
        y=box['y']+box['height']*.50
        pts=[{'x':x1,'y':y,'id':1},{'x':x2,'y':y,'id':2}]
        cdps.send('Input.dispatchTouchEvent',{'type':'touchStart','touchPoints':pts})
        page.wait_for_function('document.querySelectorAll("#pickerArea .touch-dot").length===2')
        rings=page.locator('#pickerArea .touch-dot').evaluate_all('''nodes=>nodes.map(n=>{const b=n.getBoundingClientRect(),cs=getComputedStyle(n),i=n.querySelector('.dot-fill'),ib=i.getBoundingClientRect();return {w:b.width,h:b.height,iw:ib.width,ih:ib.height,shape:cs.borderRadius,inner:getComputedStyle(i).borderRadius,left:b.left,top:b.top,color:n.style.getPropertyValue('--dot-color'),outerBorder:cs.borderStyle,innerBorder:getComputedStyle(i).borderStyle}})''')
        assert len(rings)==2
        assert rings[0]['color'] != rings[1]['color'],rings
        for ring in rings:
            assert abs(ring['w']-ring['h'])<=1,ring
            assert abs(ring['iw']-ring['ih'])<=1,ring
            assert ring['outerBorder']=='solid' and ring['innerBorder']=='solid',ring
            assert ring['shape']=='50%' and ring['inner']=='50%',ring
        first=page.locator('#pickerArea .touch-dot').first
        page.evaluate("window.__pickerFirstNode=document.querySelector('#pickerArea .touch-dot')")
        pts2=[{'x':x1+4,'y':y+10,'id':1},{'x':x2-4,'y':y+10,'id':2}]
        cdps.send('Input.dispatchTouchEvent',{'type':'touchMove','touchPoints':pts2})
        assert page.evaluate("document.querySelector('#pickerArea .touch-dot')===window.__pickerFirstNode"),w
        if w==393:
            page.screenshot(path=str(ROOT/'preview_picker_round_rings_393.png'))
        if w==320:
            page.screenshot(path=str(ROOT/'preview_picker_round_rings_320.png'))
        page.wait_for_function('document.querySelector("#pickerArea .touch-dot.winner") !== null',timeout=7500)
        assert page.locator('#pickerArea .touch-dot.winner').count()==1,w
        cdps.send('Input.dispatchTouchEvent',{'type':'touchEnd','touchPoints':[]})
        page.wait_for_function('document.querySelectorAll("#pickerArea .touch-dot").length===0',timeout=1500)
        assert not errors,(w,errors)
        page.locator('.picker-exit').click()
        assert page.locator('#toolWidgetHub').is_visible(),w
        page.close()
    browser.close()
print('PASS: round rings + concentric borders + stable touch movement + winner + reset on five sizes')

"""Browser test: normal receipt modal + useful error for Android invalid PDF."""
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).parent
HTML=(ROOT/'index.html').read_text('utf-8')
MOCK='''
window.__alerts=[];
window.__requests=[];
window.Telegram={WebApp:{initData:'query_id=demo&user=%7B%22id%22%3A123%7D',initDataUnsafe:{user:{id:123,first_name:'Тест'}},
ready(){},expand(){},onEvent(){},showAlert(m){window.__alerts.push(m);},
setHeaderColor(){},setBackgroundColor(){},setBottomBarColor(){},
HapticFeedback:{notificationOccurred(){},impactOccurred(){}},isExpanded:true,
safeAreaInset:{top:0,bottom:0},contentSafeAreaInset:{top:0,bottom:0}}};
window.fetch=async(url,opts={})=>{
 const path=String(url);window.__requests.push({path,method:opts.method||'GET'});
 if(path==='/api/events/21/receipt' && opts.method==='POST')return {ok:false,status:400,json:async()=>({error:'receipt_invalid_format'})};
 if(path==='/api/events/21/receipt')return {ok:true,status:200,json:async()=>({status:'not_submitted',title:'Настолки',price_kop:10000,payment_link:'',payment_details:''})};
 let data=path.includes('/api/admins/check')?{is_admin:false}:[];
 return {ok:true,status:200,json:async()=>data,text:async()=>JSON.stringify(data),headers:{get:()=>null}};
};
'''
with sync_playwright() as p:
  browser=p.chromium.launch(headless=True,executable_path='/usr/bin/chromium',args=['--no-sandbox'])
  for width in (320,393,430):
    page=browser.new_page(viewport={'width':width,'height':850},is_mobile=True,has_touch=True)
    page.add_init_script(MOCK)
    page.evaluate(MOCK)
    for url in ('https://fonts.googleapis.com/**','https://fonts.gstatic.com/**','https://telegram.org/**'):
      page.route(url,lambda route:route.abort())
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    page.set_content(HTML,wait_until='domcontentloaded')
    page.evaluate('''async()=>await openEventReceiptModal({id:21,title:'Настолки'})''')
    assert page.locator('#eventReceiptFile').count()==1
    assert 'скриншот' in page.locator('#eventCheckinContent').inner_text().lower()
    page.locator('#eventReceiptFile').set_input_files({'name':'receipt.pdf','mimeType':'application/pdf','buffer':b'corrupt pdf content'})
    page.locator('#sendEventReceipt').click()
    page.wait_for_function('window.__alerts.length>0')
    alerts=page.evaluate('window.__alerts')
    assert 'не вдалося розпізнати формат' in alerts[-1].lower(),alerts
    assert 'JPG' in alerts[-1]
    assert page.locator('#sendEventReceipt').is_enabled()
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1')
    assert not errors,(width,errors)
    print('PASS',width,'format-specific error, retry available, no overflow')
    page.close()
  browser.close()

// Tests the ACTUAL JS function extracted from index.html without a real payment.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync(__dirname+'/index.html','utf8');
function extract(name){
 const mark='async function '+name+'(';
 const start=html.indexOf(mark);assert(start>=0,name+' missing');
 const bodyStart=html.indexOf('{',start);let level=0;
 for(let i=bodyStart;i<html.length;i++){
   if(html[i]==='{')level++;
   if(html[i]==='}'){
     level--;if(level===0)return html.slice(start,i+1);
   }
 }
 throw Error('unterminated function '+name)
}
async function run(code,probeCode,jarMode){
 const requests=[],alerts=[],opened=[],modal=[];
 const btn={disabled:false,isConnected:true};
 const response=async(status,data)=>({ok:status>=200&&status<300,status, json:async()=>data});
 const ctx={
   telegramUserId:123,telegramInitData:'signed',lastEventsData:[{id:21,title:'Настолки',payment_method:'receipt'}],
   document:{querySelector:()=>btn},telegramAuthHeaders:()=>({}),
   tg:{showAlert:(x)=>alerts.push(x),openLink:(x)=>opened.push(x)},
   openEventReceiptModal:async(ev)=>modal.push(ev),
   loadEvents:async()=>{},localStorage:{setItem:()=>{}},
   fetch:async(url,options)=>{
     requests.push({url,method:options?.method||'GET'});
     if(url.endsWith('/payment/start'))return response(code.status,code.body);
     if(url.endsWith('/receipt'))return response(probeCode.status,probeCode.body);
     throw Error('unexpected URL '+url);
   }
 };
 vm.runInNewContext(extract('startMonobankEventPayment'),ctx,{timeout:2000});
 await ctx.startMonobankEventPayment(21);
 assert.equal(btn.disabled,false,'the button should be restored');
 return {requests,alerts,opened,modal};
}
(async()=>{
  for (const error of [{status:409,body:{error:'receipt_payment_required'}},
                       {status:503,body:{error:'monobank_not_configured'}}]) {
    const x=await run(error,{status:200,body:{status:'not_submitted'}});
    assert.equal(x.modal.length,1,'receipt form must open even from stale event');
    assert.equal(x.modal[0].id,21);
    assert.equal(x.alerts.length,0,'no false misconfiguration alert');
    assert.equal(x.requests.length,2);
  }
  const y=await run({status:503,body:{error:'monobank_not_configured'}},
                    {status:409,body:{error:'not_receipt_event'}});
  assert.equal(y.modal.length,0,'do not route genuine acquiring events into receipts');
  assert(y.alerts.some(x=>x.includes('Автоматичну оплату')));
  const z=await run({status:200,body:{status:'pending',page_url:'https://pay.mbnk.biz/abcdef'}},
                    {status:409,body:{}});
  assert.equal(z.modal.length,0);
  assert.equal(z.opened[0],'https://pay.mbnk.biz/abcdef');
  assert(!html.includes('${isPaid && !mine && !receiptMode ? `<button class="ev-payment-check"'));
  assert(html.includes('acquiringMode && !mine'));
  console.log('PASS: 5 frontend routing and regression assertions');
})().catch(err=>{console.error(err);process.exit(1)});

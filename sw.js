/* Styloteka v4.7: resilient offline game tools. Do not cache private API results. */
'use strict';
const VERSION = 'v47-20261016';
const SHELL = `styloteka-shell-${VERSION}`;
const DATA = `styloteka-public-data-${VERSION}`;
const MEDIA = 'styloteka-allowed-covers-v1';
// The UI only needs these assets to start fully offline; optional assets must not block install.
const REQUIRED = ['/offline', '/offline.html', '/offline.js', '/offline-tools.js'];
const OPTIONAL = ['/', '/offline-bridge.js', '/manifest.webmanifest', '/icons/icon-192.png', '/icons/icon-512.png', '/icons/icon-512-maskable.png'];
const MEDIA_PLACEHOLDER = `<svg xmlns="http://www.w3.org/2000/svg" width="256" height="160" viewBox="0 0 256 160"><rect width="256" height="160" rx="16" fill="#202b43"/><text x="128" y="84" font-size="52" text-anchor="middle">🎲</text></svg>`;

async function putFile(cache,path) {
  const req = new Request(new URL(path,self.location.origin),{cache:'reload'});
  const response = await fetch(req);
  if (!response.ok) throw new Error(`Offline resource ${path}: HTTP ${response.status}`);
  await cache.put(new Request(req.url),response);
}
self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL);
    await Promise.all(REQUIRED.map(path => putFile(cache,path)));
    // These are nice-to-have, but a transient 404/network issue may not break offline counters.
    await Promise.allSettled(OPTIONAL.map(path => putFile(cache,path)));
    await self.skipWaiting();
  })());
});
self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    for(const name of await caches.keys()) {
      if(name.startsWith('styloteka-shell-') && name!==SHELL) await caches.delete(name);
      if(name.startsWith('styloteka-public-data-') && name!==DATA) await caches.delete(name);
    }
    await self.clients.claim();
  })());
});

function isPublicJson(url) {
  return url.origin===self.location.origin &&
    (url.pathname==='/api/games' || (url.pathname==='/api/events' &&
      ['upcoming','history'].includes(url.searchParams.get('scope'))));
}
function isShellPath(url) {
  return url.origin===self.location.origin &&
    (url.pathname==='/' || REQUIRED.includes(url.pathname) || OPTIONAL.includes(url.pathname) || url.pathname.startsWith('/icons/'));
}
async function cachePublicResponse(cache,request,response,url) {
  if(!response.ok || response.type==='opaque')return;
  if(url.pathname==='/api/events') {
    try {
      const data=await response.clone().json();
      if(!Array.isArray(data))return;
      const keys=['id','title','description','event_date','event_time','game_name','game_names','location_text','cover_url','price_kop','max_participants','status'];
      const safe=data.map(item=>Object.fromEntries(keys.filter(k=>Object.hasOwn(item,k)).map(k=>[k,item[k]])));
      await cache.put(request,new Response(JSON.stringify(safe),{headers:{'Content-Type':'application/json; charset=utf-8','X-Styloteka-Cached':'public'}}));
    }catch(_){}
  } else await cache.put(request,response.clone());
}
async function fetchWithTimeout(request,ms=7000) {
  const controller=new AbortController();
  const timeout=setTimeout(()=>controller.abort(),ms);
  try{return await fetch(request,{signal:controller.signal})}
  finally{clearTimeout(timeout)}
}
async function publicNetworkFirst(req,url) {
  const cache=await caches.open(DATA);
  try{
    const r=await fetchWithTimeout(req);
    if(r.ok)await cachePublicResponse(cache,req,r,url);
    return r.ok?r:(await cache.match(req))||r;
  }catch(_) {
    return (await cache.match(req))||new Response('[]',{status:200,headers:{'Content-Type':'application/json','X-Styloteka-Offline-Empty':'1'}});
  }
}
async function shellFile(req,url) {
  const cache=await caches.open(SHELL);
  // Navigation to the full Telegram app needs fresh HTML when online.
  if(url.pathname==='/' && req.mode==='navigate') {
    try { return await fetchWithTimeout(req,5000); }
    catch(_) {return (await cache.match('/offline.html'))||Response.error()}
  }
  // Cache-first makes the installed offline tools work immediately with no network.
  const hit=await cache.match(req,{ignoreSearch:true});
  if(hit)return hit;
  try {
    const r=await fetchWithTimeout(req);
    if(r.ok)await cache.put(req,r.clone());
    return r;
  }catch(_) {
    if(req.mode==='navigate')return (await cache.match('/offline.html'))||Response.error();
    return Response.error();
  }
}
self.addEventListener('fetch',event=>{
  const req=event.request,url=new URL(req.url);
  if(req.method!=='GET') {
    // Never queue offline RSVP, gifts, payments or any other mutations.
    if(url.origin===self.location.origin)event.respondWith(fetch(req).catch(()=>new Response(JSON.stringify({error:'offline_action_unavailable'}),{status:503,headers:{'Content-Type':'application/json'}})));
    return;
  }
  if(isPublicJson(url)){event.respondWith(publicNetworkFirst(req,url));return}
  if(isShellPath(url)){event.respondWith(shellFile(req,url));return}
  if(req.destination==='image')event.respondWith((async()=>{
    const cache=await caches.open(MEDIA), saved=await cache.match(req);
    if(saved)return saved;
    try{return await fetchWithTimeout(req)}
    catch(_){return new Response(MEDIA_PLACEHOLDER,{headers:{'Content-Type':'image/svg+xml'}})}
  })());
});

/* Styloteka v4.5: public read-only offline shell. Sensitive responses are never cached. */
'use strict';
const VERSION = 'v45-20261016';
const SHELL = `styloteka-shell-${VERSION}`;
const DATA = `styloteka-public-data-${VERSION}`;
const MEDIA = 'styloteka-allowed-covers-v1';
const SHELL_URLS = [
  '/', '/offline', '/offline.html', '/offline.js', '/offline-bridge.js',
  '/manifest.webmanifest', '/icons/icon-192.png', '/icons/icon-512.png', '/icons/icon-512-maskable.png'
];
const MEDIA_PLACEHOLDER = `<svg xmlns="http://www.w3.org/2000/svg" width="256" height="160" viewBox="0 0 256 160"><rect width="256" height="160" rx="16" fill="#202b43"/><text x="128" y="84" font-size="52" text-anchor="middle">🎲</text></svg>`;
self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL);
    await cache.addAll(SHELL_URLS);
    await self.skipWaiting();
  })());
});
self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    for (const name of await caches.keys()) {
      if (name.startsWith('styloteka-shell-') && name !== SHELL) await caches.delete(name);
      if (name.startsWith('styloteka-public-data-') && name !== DATA) await caches.delete(name);
    }
    await self.clients.claim();
  })());
});

function isPublicJson(url) {
  return url.origin === self.location.origin &&
    (url.pathname === '/api/games' || (url.pathname === '/api/events' &&
       (url.searchParams.get('scope') === 'upcoming' || url.searchParams.get('scope') === 'history')));
}
function isShellPath(url) {
  return url.origin === self.location.origin &&
    (url.pathname === '/' || url.pathname === '/offline' || url.pathname === '/offline.html' ||
      url.pathname === '/offline.js' || url.pathname === '/offline-bridge.js' ||
      url.pathname === '/manifest.webmanifest' || url.pathname.startsWith('/icons/'));
}
async function cachePublicResponse(cache, request, response, url) {
  if (!response.ok || response.type === 'opaque') return;
  if (url.pathname === '/api/events') {
    // Do not persist usernames/Telegram IDs/RSVP lists in offline storage.
    try {
      const data = await response.clone().json();
      if (!Array.isArray(data)) return;
      const rows = data.map(item => {
        const allowed = ['id','title','description','event_date','event_time','game_name',
          'game_names','location_text','cover_url','price_kop','max_participants','status'];
        return Object.fromEntries(allowed.filter(k => Object.hasOwn(item,k)).map(k => [k,item[k]]));
      });
      const safe = new Response(JSON.stringify(rows), {headers:{'Content-Type':'application/json; charset=utf-8','X-Styloteka-Cached':'public'}});
      await cache.put(request, safe);
    } catch (_) { /* invalid response: do not cache */ }
  } else {
    await cache.put(request,response.clone());
  }
}
async function fetchWithTimeout(request, ms=9000) {
  const controller = new AbortController();
  const timeout = setTimeout(()=>controller.abort(),ms);
  try { return await fetch(request,{signal:controller.signal}); }
  finally { clearTimeout(timeout); }
}
async function networkFirst(request, cacheName, url, time=9000) {
  const cache = await caches.open(cacheName);
  try {
    const resp = await fetchWithTimeout(request,time);
    if (resp.ok) {
      if (cacheName === DATA) await cachePublicResponse(cache, request, resp, url);
      else await cache.put(request,resp.clone());
      return resp;
    }
    return (await cache.match(request,{ignoreSearch:cacheName===SHELL})) || resp;
  } catch (_) {
    const cached = await cache.match(request,{ignoreSearch:cacheName===SHELL});
    if (cached) return cached;
    if (cacheName === DATA) return new Response('[]',{status:200,headers:{'Content-Type':'application/json','X-Styloteka-Offline-Empty':'1'}});
    return (await caches.match('/offline.html')) || new Response('Offline — open Styloteka after an online visit',{status:503,headers:{'Content-Type':'text/plain'}});
  }
}
self.addEventListener('fetch',event => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== 'GET') {
    // Prevent any offline write. Payment/RSVP/shop updates always require the server.
    if (!url.origin || url.origin !== self.location.origin) return;
    event.respondWith(fetch(req).catch(() => new Response(JSON.stringify({error:'offline_action_unavailable'}),{status:503,headers:{'Content-Type':'application/json'}})));
    return;
  }
  if (isPublicJson(url)) {
    event.respondWith(networkFirst(req,DATA,url));
    return;
  }
  if (isShellPath(url)) {
    event.respondWith(networkFirst(req,SHELL,url,6000));
    return;
  }
  // Only already authorized cover URLs that the user explicitly saved are served from MEDIA.
  if (req.destination==='image') {
    event.respondWith((async()=>{
      const cache=await caches.open(MEDIA);
      const saved=await cache.match(req);
      if (saved) return saved;
      try {return await fetchWithTimeout(req,7000);}
      catch (_) {return new Response(MEDIA_PLACEHOLDER,{headers:{'Content-Type':'image/svg+xml'}});}
    })());
  }
});

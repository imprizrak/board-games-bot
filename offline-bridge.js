/* Styloteka PWA bridge — shared with Telegram Mini App */
(() => {
  'use strict';
  if (!('serviceWorker' in navigator) || !window.isSecureContext) return;
  function updateRibbon() {
    const ribbon = document.getElementById('stylotekaOfflineRibbon');
    if (ribbon) ribbon.hidden = navigator.onLine;
    document.body?.classList.toggle('styloteka-offline-active', !navigator.onLine);
  }
  window.addEventListener('online', updateRibbon);
  window.addEventListener('offline', updateRibbon);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', updateRibbon);
  else updateRibbon();
  window.addEventListener('load', async () => {
    try { await navigator.serviceWorker.register('/sw.js', {scope:'/'}); }
    catch(e) { console.warn('Offline mode is unavailable:', e); }
  }, {once:true});
})();

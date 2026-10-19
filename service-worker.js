const CACHE='kaiclip-shell-v1';
const ASSETS=['/','/static/styles.css','/static/app.js','/static/icon.svg','/static/manifest.webmanifest'];
self.addEventListener('install',event=>{event.waitUntil(caches.open(CACHE).then(c=>c.addAll(ASSETS)));self.skipWaiting()});
self.addEventListener('activate',event=>{event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==CACHE).map(k=>caches.delete(k)))));self.clients.claim()});
self.addEventListener('fetch',event=>{
 const req=event.request;
 // Never cache uploads, job states, clips, or headers carrying a PIN.
 if(req.method!=='GET'||!new URL(req.url).pathname.match(/^\/(static\/|$)/))return;
 event.respondWith(fetch(req).then(res=>{if(res.ok){const clone=res.clone();caches.open(CACHE).then(c=>c.put(req,clone))}return res}).catch(()=>caches.match(req)));
});

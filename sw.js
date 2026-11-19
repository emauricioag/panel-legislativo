// Service worker del panel: permite instalarlo como app y abrirlo sin conexión con los últimos datos.
// La página y los datos se piden siempre a la red primero (así revisarVersion() ve el HTML nuevo);
// solo si no hay conexión se usa la copia guardada. Fotos, íconos y fuentes salen de la copia.
const CACHE = 'panel-v1';
const BASICO = ['./', 'index.html', 'manifest.webmanifest', 'assets/escudo-corrientes.svg', 'assets/icono-192.png',
  'data/base.json', 'data/live.json', 'data/diputados.json'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(BASICO)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const r = e.request;
  if (r.method !== 'GET') return;
  const u = new URL(r.url);
  const propio = u.origin === location.origin;
  const fuente = /fonts\.(googleapis|gstatic)\.com$/.test(u.hostname);
  if (!propio && !fuente) return;
  const clave = propio ? u.origin + u.pathname : r.url;   // sin el ?v= que agrega la página
  const red = propio && (r.mode === 'navigate' || /\.(html|json|webmanifest)$/.test(u.pathname) || u.pathname.endsWith('/'));
  if (red) {
    e.respondWith(fetch(r).then(resp => {
      if (resp.ok) { const copia = resp.clone(); caches.open(CACHE).then(c => c.put(clave, copia)); }
      return resp;
    }).catch(() => caches.match(clave).then(m => m || (r.mode === 'navigate' ? caches.match('index.html') : Response.error()))));
  } else {
    e.respondWith(caches.match(clave).then(m => m || fetch(r).then(resp => {
      if (resp.ok || resp.type === 'opaque') { const copia = resp.clone(); caches.open(CACHE).then(c => c.put(clave, copia)); }
      return resp;
    })));
  }
});

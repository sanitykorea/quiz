// 홈 화면 아이콘으로 열 때 서버(Render 무료 플랜은 15분 놀면 잠든다)를 기다리지 않게 한다.
// 껍데기는 캐시에서 즉시 꺼내 그리고, 새 버전은 뒤에서 받아 다음 실행에 반영한다.
const CACHE = 'tower-shell-v1';
const SHELL = ['/', '/manifest.json', '/icon-192.png', '/icon-512.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(caches.keys()
    .then(ks => Promise.all(ks.filter(k => k !== CACHE).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith('/api/')) return;   // 데이터는 절대 캐시하지 않는다 — 항상 서버가 기준

  const isShell = url.pathname === '/' || SHELL.includes(url.pathname);
  if (!isShell) return;

  // 캐시 우선 + 뒤에서 갱신(stale-while-revalidate)
  e.respondWith(caches.open(CACHE).then(async cache => {
    const hit = await cache.match(url.pathname === '/' ? '/' : e.request);
    const fresh = fetch(e.request).then(res => {
      if (res && res.ok) cache.put(url.pathname === '/' ? '/' : e.request, res.clone());
      return res;
    }).catch(() => null);
    return hit || (await fresh) || new Response('오프라인이에요', {status: 503, headers: {'Content-Type': 'text/plain; charset=utf-8'}});
  }));
});

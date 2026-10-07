// 홈 화면 아이콘으로 열 때 서버(Render 무료 플랜은 15분 놀면 잠든다)를 기다리지 않게 한다.
// 화면(HTML)은 "빠른 쪽이 이긴다": 2.5초 안에 서버가 답하면 새 버전, 아니면 캐시된 화면.
// 그래서 배포한 내용이 다음 접속에 바로 보이고, 서버가 자고 있어도 즉시 뜬다.
const CACHE = 'tower-shell-v2';
const SHELL = ['/', '/manifest.json', '/icon-192.png', '/icon-512.png'];
const NET_TIMEOUT = 2500;

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
  if (url.pathname.startsWith('/api/')) return;   // 데이터는 캐시하지 않는다 — 항상 서버가 기준

  const isDoc = e.request.mode === 'navigate' || url.pathname === '/';
  if (!isDoc && !SHELL.includes(url.pathname)) return;

  e.respondWith(caches.open(CACHE).then(async cache => {
    const key = isDoc ? '/' : e.request;
    const hit = await cache.match(key);
    const net = fetch(e.request).then(res => {
      if (res && res.ok) cache.put(key, res.clone());
      return res;
    }).catch(() => null);

    if (!isDoc) return hit || (await net) || new Response('', {status: 504});
    if (!hit) return (await net) || new Response('오프라인이에요', {status: 503, headers: {'Content-Type': 'text/plain; charset=utf-8'}});
    // 캐시가 있으면 잠깐만 기다려 본다 — 서버가 깨어 있으면 새 화면, 자고 있으면 캐시
    const raced = await Promise.race([net, new Promise(r => setTimeout(() => r(null), NET_TIMEOUT))]);
    return raced || hit;
  }));
});

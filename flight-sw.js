/* 비행 위치 화면 전용 서비스워커 — 기내(오프라인)에서도 열리도록 캐시.
   주의: 'flight' 관련 요청만 가로채고 나머지는 손대지 않는다(기존 앱 영향 X). */
const CACHE = "flight-offline-v1";

self.addEventListener("install", e => self.skipWaiting());
self.addEventListener("activate", e => e.waitUntil(self.clients.claim()));

function mine(url) {
  return url.origin === self.location.origin && /flight/.test(url.pathname);
}

self.addEventListener("fetch", e => {
  const req = e.request;
  if (req.method !== "GET") return;
  let url;
  try { url = new URL(req.url); } catch (_) { return; }
  if (!mine(url)) return;                     // 기존 앱 요청은 그대로 통과

  e.respondWith((async () => {
    const cache = await caches.open(CACHE);
    const hit = await cache.match(req, { ignoreSearch: true });
    if (hit) {                                 // 캐시 우선 — 오프라인에서도 즉시 표시
      fetch(req).then(r => { if (r.ok) cache.put(req, r.clone()); }).catch(() => {});
      return hit;
    }
    try {
      const res = await fetch(req);
      if (res.ok) cache.put(req, res.clone());
      return res;
    } catch (err) {
      const any = await cache.match("./flight.html") || await cache.match("/flight");
      if (any) return any;
      throw err;
    }
  })());
});

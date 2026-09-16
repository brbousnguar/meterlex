const CACHE = 'meterlex-v1'
const SHELL = ['/']

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)))
  self.skipWaiting()
})

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    )
  )
  self.clients.claim()
})

// /api/* is never cached: a meter reading from an hour ago is not a reading.
// Everything else is network-first, so a new build always wins when online.
self.addEventListener('fetch', (e) => {
  if (e.request.method !== 'GET') return
  if (!e.request.url.startsWith(self.location.origin)) return
  if (new URL(e.request.url).pathname.startsWith('/api/')) return

  e.respondWith(
    fetch(e.request)
      .then((res) => {
        const copy = res.clone()
        caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {})
        return res
      })
      .catch(() => caches.match(e.request).then((hit) => hit || caches.match('/')))
  )
})

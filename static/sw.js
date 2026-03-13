// ═══════════════════════════════════════════════════════
//  Guess Up — Service Worker
//  بيخزّن الملفات الأساسية عشان اللعبة تشتغل أسرع
// ═══════════════════════════════════════════════════════

const CACHE_NAME = 'guessup-v1';

// الملفات اللي هتتخزن offline
const STATIC_ASSETS = [
    '/',
    '/static/sounds/click.mp3',
    '/static/sounds/start.mp3',
    '/static/sounds/win.mp3',
    '/static/sounds/cheat.mp3',
    '/static/icons/icon-192x192.png',
    '/static/icons/icon-512x512.png',
    '/static/manifest.json'
];

// ── Install: خزّن الملفات ──────────────────────────────
self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME).then(cache => {
            console.log('[SW] Caching static assets');
            // نحاول نخزن كل ملف بشكل منفصل عشان لو ملف فشل مش يوقف الباقي
            return Promise.allSettled(
                STATIC_ASSETS.map(url =>
                    cache.add(url).catch(err =>
                        console.warn('[SW] Failed to cache:', url, err)
                    )
                )
            );
        }).then(() => self.skipWaiting())
    );
});

// ── Activate: امسح الـ cache القديم ───────────────────
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys =>
            Promise.all(
                keys.filter(k => k !== CACHE_NAME)
                    .map(k => { console.log('[SW] Deleting old cache:', k); return caches.delete(k); })
            )
        ).then(() => self.clients.claim())
    );
});

// ── Fetch: استراتيجية Network First ───────────────────
// يحاول يجيب من الإنترنت أولاً — لو فشل يرجع الـ cache
self.addEventListener('fetch', event => {
    const url = new URL(event.request.url);

    // الـ SocketIO requests متخزنهاش أبداً
    if (url.pathname.startsWith('/socket.io')) return;

    // API calls متخزنهاش
    if (url.pathname.startsWith('/api/') ||
        url.pathname.startsWith('/admin') ||
        url.pathname.startsWith('/create_room') ||
        url.pathname.startsWith('/join_room') ||
        url.pathname.startsWith('/daily_reward')) return;

    // الصور والأصوات → Cache First (أسرع)
    if (event.request.destination === 'image' ||
        event.request.destination === 'audio') {
        event.respondWith(
            caches.match(event.request).then(cached => {
                return cached || fetch(event.request).then(response => {
                    const clone = response.clone();
                    caches.open(CACHE_NAME).then(c => c.put(event.request, clone));
                    return response;
                });
            })
        );
        return;
    }

    // باقي الملفات → Network First
    event.respondWith(
        fetch(event.request)
            .then(response => {
                // خزّن نسخة في الـ cache
                if (response.ok && event.request.method === 'GET') {
                    const clone = response.clone();
                    caches.open(CACHE_NAME).then(c => c.put(event.request, clone));
                }
                return response;
            })
            .catch(() => {
                // لو الإنترنت مش موجود → رجّع من الـ cache
                return caches.match(event.request);
            })
    );
});
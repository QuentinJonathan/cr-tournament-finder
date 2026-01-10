// CR Tournament Finder - Service Worker
// Provides offline caching for static assets

const CACHE_NAME = 'cr-finder-v1';
const STATIC_ASSETS = [
    '/',
    '/static/style.css',
    '/static/app.js',
    '/static/icons/icon-192x192.png',
    '/static/icons/icon-512x512.png',
    '/static/icons/apple-touch-icon.png',
    '/static/icons/favicon.ico'
];

// Install: Cache static assets
self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => {
                console.log('[SW] Caching static assets');
                return cache.addAll(STATIC_ASSETS);
            })
            .then(() => self.skipWaiting())
    );
});

// Activate: Clean up old caches
self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys()
            .then((cacheNames) => {
                return Promise.all(
                    cacheNames
                        .filter((name) => name !== CACHE_NAME)
                        .map((name) => {
                            console.log('[SW] Deleting old cache:', name);
                            return caches.delete(name);
                        })
                );
            })
            .then(() => self.clients.claim())
    );
});

// Fetch: Network-first for HTML/API, Cache-first for static assets
self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);

    // Skip non-GET requests
    if (event.request.method !== 'GET') {
        return;
    }

    // Skip API calls and auth endpoints - always go to network
    if (url.pathname.startsWith('/api/') ||
        url.pathname === '/login' ||
        url.pathname === '/logout') {
        return;
    }

    // For static assets: Cache-first strategy
    if (url.pathname.startsWith('/static/')) {
        event.respondWith(
            caches.match(event.request)
                .then((cached) => {
                    if (cached) {
                        return cached;
                    }
                    return fetch(event.request)
                        .then((response) => {
                            // Cache new static assets
                            if (response.ok) {
                                const clone = response.clone();
                                caches.open(CACHE_NAME).then((cache) => {
                                    cache.put(event.request, clone);
                                });
                            }
                            return response;
                        });
                })
        );
        return;
    }

    // For HTML (main page): Network-first with cache fallback
    if (event.request.mode === 'navigate' ||
        event.request.headers.get('accept')?.includes('text/html')) {
        event.respondWith(
            fetch(event.request)
                .then((response) => {
                    // Cache successful HTML responses
                    if (response.ok) {
                        const clone = response.clone();
                        caches.open(CACHE_NAME).then((cache) => {
                            cache.put(event.request, clone);
                        });
                    }
                    return response;
                })
                .catch(() => {
                    // Offline: Return cached HTML
                    return caches.match(event.request)
                        .then((cached) => {
                            if (cached) {
                                return cached;
                            }
                            // Ultimate fallback: offline message
                            return new Response(
                                `<!DOCTYPE html>
                                <html>
                                <head>
                                    <meta charset="UTF-8">
                                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                                    <title>Offline - CR Tournament Finder</title>
                                    <style>
                                        body {
                                            font-family: 'Inter', system-ui, sans-serif;
                                            background: #0d0d1a;
                                            color: #e0e0e0;
                                            display: flex;
                                            align-items: center;
                                            justify-content: center;
                                            min-height: 100vh;
                                            margin: 0;
                                            text-align: center;
                                        }
                                        .offline-container {
                                            padding: 2rem;
                                        }
                                        h1 {
                                            color: #f4d03f;
                                            font-size: 2rem;
                                            margin-bottom: 1rem;
                                        }
                                        p {
                                            color: #a0a0b0;
                                            margin-bottom: 1.5rem;
                                        }
                                        button {
                                            background: #f4d03f;
                                            color: #0d0d1a;
                                            border: none;
                                            padding: 12px 24px;
                                            border-radius: 8px;
                                            font-weight: 600;
                                            cursor: pointer;
                                        }
                                        button:hover {
                                            background: #e6c52e;
                                        }
                                    </style>
                                </head>
                                <body>
                                    <div class="offline-container">
                                        <h1>Offline</h1>
                                        <p>Die App benötigt eine Internetverbindung, um Turniere zu suchen.</p>
                                        <button onclick="location.reload()">Erneut versuchen</button>
                                    </div>
                                </body>
                                </html>`,
                                {
                                    headers: { 'Content-Type': 'text/html; charset=utf-8' }
                                }
                            );
                        });
                })
        );
        return;
    }
});

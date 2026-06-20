const CACHE_NAME = 'football-pwa-v1';

// نصب سرویس ورکر
self.addEventListener('install', (event) => {
    self.skipWaiting();
});

// فعال‌سازی
self.addEventListener('activate', (event) => {
    event.waitUntil(clients.claim());
});

// اجباری‌ترین بخش برای PWA شدن: رهگیری درخواست‌ها
self.addEventListener('fetch', (event) => {
    event.respondWith(fetch(event.request));
});

// Minimal service worker — enables "Add to Home Screen" on iOS/Android.
// No offline caching yet (requires auth, so cache-first would break login).
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(clients.claim()));

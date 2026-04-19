self.addEventListener('install', (e) => {
  console.log('Service Worker: Installed');
});

self.addEventListener('fetch', (e) => {
  // Just a pass-through
  e.respondWith(fetch(e.request));
});

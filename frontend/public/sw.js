// CakeCRM service worker — Web Push (issue #6).
// Served at /sw.js in dev (Vite public/) and prod (backend SPA fallback serves any
// real file in dist), so the registration scope "/" works. Icon/badge are omitted
// deliberately — no bundled push-icon asset exists; browsers render a default.

self.addEventListener('push', (event) => {
  const data = event.data ? event.data.json() : {};
  const title = data.title || 'CakeCRM';
  const options = {
    body: data.body || '',
    tag: data.notification_id || 'cakecrm-notification',
    data: { url: data.url || '/crm' },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = event.notification.data?.url || '/crm';
  event.waitUntil(
    clients.matchAll({ type: 'window' }).then((windowClients) => {
      for (const client of windowClients) {
        if (client.url.includes(url) && 'focus' in client) {
          return client.focus();
        }
      }
      return clients.openWindow(url);
    })
  );
});

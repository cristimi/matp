self.addEventListener('push', (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (e) {
    payload = { title: 'MATP', body: event.data ? event.data.text() : '' };
  }

  const title = payload.title || 'MATP';
  const options = {
    body: payload.body || '',
    tag: payload.tag,
    renotify: !!payload.renotify,
    data: payload.data || {},
    icon: '/icon-192.png',
    badge: '/icon-192.png',
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const positionId = event.notification.data && event.notification.data.position_id;
  const url = positionId ? '/positions' : '/';

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if ('focus' in client) {
          client.navigate(url);
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(url);
      }
    })
  );
});

// Class Sync Firebase Cloud Messaging Service Worker
// Served at root scope (/firebase-messaging-sw.js)

importScripts("https://www.gstatic.com/firebasejs/10.14.1/firebase-app-compat.js");
importScripts("https://www.gstatic.com/firebasejs/10.14.1/firebase-messaging-compat.js");

const firebaseConfig = {
  apiKey: "{{ FIREBASE_CONFIG.apiKey|default:'AIzaSyDWnb-VD5qEsYgGSKMuT_aSrEORMuswh34' }}",
  authDomain: "{{ FIREBASE_CONFIG.authDomain|default:'classsync-6eac2.firebaseapp.com' }}",
  projectId: "{{ FIREBASE_CONFIG.projectId|default:'classsync-6eac2' }}",
  storageBucket: "{{ FIREBASE_CONFIG.storageBucket|default:'classsync-6eac2.firebasestorage.app' }}",
  messagingSenderId: "{{ FIREBASE_CONFIG.messagingSenderId|default:'224054122688' }}",
  appId: "{{ FIREBASE_CONFIG.appId|default:'1:224054122688:web:338f5f00d226c0fec6b525' }}"
};

firebase.initializeApp(firebaseConfig);
const messaging = firebase.messaging();

// Background push notification handler (when tab is closed or in background)
messaging.onBackgroundMessage((payload) => {
  console.log("[ClassSync SW] Received background message:", payload);

  const title = payload.notification?.title || payload.data?.title || "Class Sync";
  const body = payload.notification?.body || payload.data?.body || payload.data?.message || "You have a new update.";
  const clickAction = payload.data?.click_action || "/notifications/";

  const notificationOptions = {
    body: body,
    icon: "/static/img/icon.png",
    badge: "/static/img/badge.png",
    tag: payload.data?.type || "classsync-notification",
    data: {
      url: clickAction,
      ...payload.data
    },
    actions: [
      { action: "open", title: "View" }
    ]
  };

  return self.registration.showNotification(title, notificationOptions);
});

// Click handler: opens or focuses the relevant ClassSync page
self.addEventListener("notificationclick", (event) => {
  event.notification.close();

  const targetUrl = event.notification.data?.url || "/notifications/";
  const absoluteUrl = new URL(targetUrl, self.location.origin).href;

  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((windowClients) => {
      for (const client of windowClients) {
        if ("focus" in client && client.url.startsWith(self.location.origin)) {
          client.navigate(absoluteUrl);
          return client.focus();
        }
      }
      if (clients.openWindow) {
        return clients.openWindow(absoluteUrl);
      }
    })
  );
});

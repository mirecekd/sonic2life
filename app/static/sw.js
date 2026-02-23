/**
 * Sonic2Life Service Worker
 * - Caches static assets for offline shell
 * - Handles Web Push notifications with action buttons
 * - Forwards push events to open windows via postMessage
 * - Sends notification responses to server on action click
 */

const CACHE_NAME = "sonic2life-v6";
const STATIC_ASSETS = [
    "/",
    "/static/style.css",
    "/static/app.js",
    "/static/manifest.json",
    "/static/icons/icon-192.png",
    "/static/icons/icon-512.png",
    "/static/icons/badge-96.png",
];

// ── Install: cache static assets ─────────────────────────────────────
// Note: cache.addAll() does NOT send credentials (cookies/Basic Auth).
// Behind nginx Basic Auth, this causes 401 errors. We use manual fetch
// with credentials: "include" to work behind HTTP Basic Auth proxies.
self.addEventListener("install", function (event) {
    console.log("[SW] Install v6");
    event.waitUntil(
        caches.open(CACHE_NAME).then(function (cache) {
            return Promise.all(
                STATIC_ASSETS.map(function (url) {
                    return fetch(url, { credentials: "include" })
                        .then(function (response) {
                            if (!response.ok) {
                                console.warn("[SW] Failed to cache:", url, response.status);
                                return; // skip this asset, don't break install
                            }
                            return cache.put(url, response);
                        })
                        .catch(function (err) {
                            console.warn("[SW] Cache fetch error:", url, err);
                        });
                })
            );
        })
    );
    self.skipWaiting();
});

// ── Activate: clean old caches ───────────────────────────────────────
self.addEventListener("activate", function (event) {
    console.log("[SW] Activate v6");
    event.waitUntil(
        caches.keys().then(function (names) {
            return Promise.all(
                names
                    .filter(function (name) { return name !== CACHE_NAME; })
                    .map(function (name) { return caches.delete(name); })
            );
        })
    );
    self.clients.claim();
});

// ── Fetch: network first, fallback to cache ──────────────────────────
self.addEventListener("fetch", function (event) {
    // Skip WebSocket, API, and SSE requests
    if (
        event.request.url.includes("/ws/") ||
        event.request.url.includes("/api/") ||
        event.request.url.includes("/health")
    ) {
        return;
    }

    event.respondWith(
        fetch(event.request, { credentials: "include" })
            .then(function (response) {
                // Cache successful GET responses
                if (response.ok && event.request.method === "GET") {
                    var clone = response.clone();
                    caches.open(CACHE_NAME).then(function (cache) {
                        cache.put(event.request, clone);
                    });
                }
                return response;
            })
            .catch(function () {
                return caches.match(event.request);
            })
    );
});

// ── Push: display notification ───────────────────────────────────────
self.addEventListener("push", function (event) {
    console.log("[SW] Push received");

    var data = { title: "Sonic2Life", body: "You have a new message", icon: "/static/icons/icon-192.png" };

    if (event.data) {
        try {
            data = event.data.json();
        } catch (e) {
            data.body = event.data.text();
        }
    }

    // Build system notification actions from payload
    var notifActions = [];
    if (data.actions && data.actions.length > 0) {
        notifActions = data.actions.map(function (a) {
            return { action: a.action, title: a.title };
        });
    } else {
        notifActions = [
            { action: "open", title: "Open" },
            { action: "dismiss", title: "Close" },
        ];
    }

    var options = {
        body: data.body || "Reminder",
        icon: data.icon || "/static/icons/icon-192.png",
        badge: "/static/icons/badge-96.png",
        vibrate: [200, 100, 200],
        tag: data.tag || "sonic2life-notification",
        renotify: true,
        requireInteraction: true,
        data: {
            url: data.url || "/",
            notification_id: data.notification_id || "",
        },
        actions: notifActions,
    };

    event.waitUntil(
        Promise.all([
            // Show system notification
            self.registration.showNotification(data.title || "Sonic2Life", options),
            // Forward to open app windows for in-app banner
            self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (windowClients) {
                windowClients.forEach(function (client) {
                    client.postMessage({
                        type: "push-notification",
                        title: data.title || "Sonic2Life",
                        body: data.body || "Reminder",
                        tag: data.tag || "sonic2life",
                        url: data.url || "/",
                        notification_id: data.notification_id || "",
                        actions: data.actions || [],
                    });
                });
            }),
        ])
    );
});

// ── Notification click: handle action + send response to server ──────
self.addEventListener("notificationclick", function (event) {
    var action = event.action || "open";
    var notifId = event.notification.data && event.notification.data.notification_id ? event.notification.data.notification_id : "";
    var url = event.notification.data && event.notification.data.url ? event.notification.data.url : "/";

    console.log("[SW] Notification clicked: action=" + action + " id=" + notifId);
    event.notification.close();

    // If user just dismissed, send response and stop
    if (action === "dismiss") {
        if (notifId) {
            event.waitUntil(sendNotificationResponse(notifId, action));
        }
        return;
    }

    // Send action response to server (if we have a notification_id)
    var responsePromise = notifId
        ? sendNotificationResponse(notifId, action)
        : Promise.resolve();

    // Focus or open the app
    var focusPromise = self.clients
        .matchAll({ type: "window", includeUncontrolled: true })
        .then(function (windowClients) {
            for (var i = 0; i < windowClients.length; i++) {
                if (windowClients[i].url.includes(self.location.origin)) {
                    return windowClients[i].focus();
                }
            }
            return self.clients.openWindow(url);
        });

    event.waitUntil(Promise.all([responsePromise, focusPromise]));
});

/**
 * Send notification response to server via fetch.
 */
function sendNotificationResponse(notifId, action) {
    return fetch(self.location.origin + "/api/push/respond", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            notification_id: notifId,
            action: action,
            source: "system_notification",
        }),
    }).then(function (resp) {
        console.log("[SW] Response sent for " + notifId + ": " + action + " (status " + resp.status + ")");
    }).catch(function (err) {
        console.warn("[SW] Failed to send response:", err);
    });
}

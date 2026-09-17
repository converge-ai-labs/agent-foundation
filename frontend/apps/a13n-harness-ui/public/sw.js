// Notifications only: no fetch handler, offline cache, credentials, or queued writes.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) =>
  event.waitUntil(self.clients.claim()),
);

self.addEventListener("push", (event) => {
  event.waitUntil(
    (async () => {
      let notice = {};
      try {
        notice = event.data?.json() || {};
      } catch {
        // Even an invalid payload produces a visible, non-sensitive reminder.
      }
      const path =
        typeof notice.path === "string" &&
        (/^\/threads\/[^/?#]+$/.test(notice.path) ||
          notice.path === "/settings/notifications")
          ? notice.path
          : "/";
      await self.registration.showNotification(
        typeof notice.title === "string"
          ? notice.title.slice(0, 160)
          : "Harness UI",
        {
          body:
            typeof notice.body === "string"
              ? notice.body.slice(0, 360)
              : "Open Harness UI to check your conversations.",
          tag:
            typeof notice.tag === "string"
              ? notice.tag.slice(0, 200)
              : "a13n-harness-ui.notice",
          icon: "/icons/icon-192.png",
          data: { path },
        },
      );
    })(),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    (async () => {
      const target = new URL(
        event.notification.data?.path || "/",
        self.location.origin,
      );
      if (target.origin !== self.location.origin) return;
      const windows = await self.clients.matchAll({
        type: "window",
        includeUncontrolled: true,
      });
      for (const window of windows) {
        // Never reload another page: it may own unsaved in-memory editors.
        // Reuse only a window already showing this exact target.
        if (window.url !== target.href) continue;
        await window.focus();
        return;
      }
      await self.clients.openWindow(target.href);
    })(),
  );
});

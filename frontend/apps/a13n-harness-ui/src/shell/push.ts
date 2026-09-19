import { result, type Transport } from "../transport/client";
import { readPreference, writePreference } from "./preferences";

const SUBSCRIPTION = "notifications.push-subscription";
export const pushSubscriptionId = () => readPreference(SUBSCRIPTION, "");
export const supportsPush = () =>
  window.isSecureContext &&
  typeof Notification !== "undefined" &&
  "serviceWorker" in navigator &&
  "PushManager" in window;

async function serialized<T>(operation: () => Promise<T>): Promise<T> {
  return navigator.locks
    ? navigator.locks.request("a13n-harness-ui.push", operation)
    : operation();
}

function keyBytes(key: string): Uint8Array<ArrayBuffer> {
  const value = atob(
    key.replace(/-/g, "+").replace(/_/g, "/") +
      "=".repeat((4 - (key.length % 4)) % 4),
  );
  return Uint8Array.from(value, (character) => character.charCodeAt(0));
}

export async function notificationWorker(): Promise<ServiceWorkerRegistration> {
  await navigator.serviceWorker.register("/sw.js", {
    scope: "/",
    updateViaCache: "none",
  });
  let timeout: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      navigator.serviceWorker.ready,
      new Promise<never>((_, reject) => {
        timeout = setTimeout(
          () =>
            reject(
              new Error("Background notification setup timed out. Try again."),
            ),
          15000,
        );
      }),
    ]);
  } finally {
    clearTimeout(timeout);
  }
}

export async function enablePush(
  transport: Transport,
  prompt = true,
): Promise<void> {
  if (!supportsPush())
    throw new Error(
      "Background notifications require a supported browser on HTTPS. On iPhone or iPad, add Harness UI to the Home Screen first.",
    );
  // Request directly from the user's gesture, before network or worker awaits.
  const permission =
    prompt && Notification.permission === "default"
      ? await Notification.requestPermission()
      : Notification.permission;
  if (permission !== "granted")
    throw new Error(
      "Allow notifications in this site's browser settings first.",
    );
  await serialized(async () => {
    if (readPreference("notifications.enabled", "true") === "false") return;
    // A queued refresh must not undo a concurrent explicit disable.
    if (!prompt && !pushSubscriptionId()) return;
    const previousId = pushSubscriptionId();
    const configuration = await result(
      transport.client.GET("/api/push/configuration", {
        signal: AbortSignal.timeout(15000),
      }),
    );
    const registration = await notificationWorker();
    const publicKey = keyBytes(configuration.public_key);
    let subscription = await registration.pushManager.getSubscription();
    const existingKey = subscription?.options.applicationServerKey;
    const keyChanged =
      !!existingKey &&
      String(new Uint8Array(existingKey)) !== String(publicKey);
    if (!prompt && (!subscription || keyChanged))
      throw new Error(
        "This device's push subscription has expired. Reconnect background notifications.",
      );
    // An explicit reconnect must replace endpoints rejected by the provider,
    // even when getSubscription() still returns the old browser object.
    if (subscription && (keyChanged || (prompt && previousId))) {
      await subscription.unsubscribe();
      subscription = null;
    }
    const untracked = !subscription || !previousId;
    if (!subscription) {
      try {
        subscription = await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: publicKey,
        });
      } catch (error) {
        const detail =
          error instanceof Error
            ? `${error.name}: ${error.message}`
            : String(error);
        const guidance = /push service|AbortError/i.test(detail)
          ? "Check this device's push service and network access; on Android Chrome, check Google Play services and any VPN or firewall."
          : "Check this site's notification permission and browser push support.";
        throw new Error(
          `Browser push registration failed before a subscription could be saved to Harness UI. ${guidance} Browser detail: ${detail}`,
          { cause: error },
        );
      }
    }
    try {
      const data = subscription.toJSON();
      if (!data.keys?.p256dh || !data.keys.auth)
        throw new Error(
          "This browser did not return a complete push subscription.",
        );
      // The user may turn reminders off while the permission prompt or worker is pending.
      if (readPreference("notifications.enabled", "true") === "false") {
        await subscription.unsubscribe();
        return;
      }
      const saved = await result(
        transport.client.PUT("/api/push/subscription", {
          signal: AbortSignal.timeout(15000),
          body: {
            endpoint: subscription.endpoint,
            keys: { p256dh: data.keys.p256dh, auth: data.keys.auth },
            origin: window.location.origin,
          },
        }),
      );
      writePreference(SUBSCRIPTION, saved.subscription_id);
      // Storage is required to reconcile/disable the same subscription after reload.
      if (pushSubscriptionId() !== saved.subscription_id) {
        await subscription.unsubscribe();
        await transport.client.DELETE(
          "/api/push/subscriptions/{subscription_id}",
          {
            signal: AbortSignal.timeout(15000),
            params: { path: { subscription_id: saved.subscription_id } },
          },
        );
        throw new Error(
          "Browser storage is unavailable; background notifications were not enabled.",
        );
      }
    } catch (error) {
      // A lost PUT response may still have saved the endpoint. Invalidate new
      // subscriptions so an unsuccessful opt-in cannot leave invisible delivery.
      // Keep an already tracked subscription on a transient refresh failure.
      if (untracked) await subscription.unsubscribe();
      throw error;
    }
  });
}

export async function disablePush(transport?: Transport): Promise<void> {
  await serialized(async () => {
    const id = pushSubscriptionId();
    // Invalidate the browser subscription even if the server is unreachable.
    let browserRemoved = false;
    try {
      if (supportsPush()) {
        const registration = await navigator.serviceWorker.getRegistration("/");
        const subscription = await registration?.pushManager.getSubscription();
        browserRemoved = !subscription || (await subscription.unsubscribe());
        for (const notice of (await registration?.getNotifications()) ?? [])
          notice.close();
      }
    } catch {
      // Still attempt server removal when the browser API fails.
    }
    let serverRemoved = false;
    try {
      if (id && transport) {
        await transport.client.DELETE(
          "/api/push/subscriptions/{subscription_id}",
          {
            signal: AbortSignal.timeout(15000),
            params: { path: { subscription_id: id } },
          },
        );
        serverRemoved = true;
      }
    } catch {
      // Invalidating either side prevents further delivery to this subscription.
    }
    if (browserRemoved || serverRemoved || !id)
      writePreference(SUBSCRIPTION, "");
    else
      throw new Error(
        "Could not disable background notifications. Check browser site permissions and try again.",
      );
  });
}

export async function reportPushActivity(
  transport: Transport,
  signal: AbortSignal,
): Promise<void> {
  await serialized(async () => {
    const id = pushSubscriptionId();
    if (
      signal.aborted ||
      !id ||
      !supportsPush() ||
      readPreference("notifications.enabled", "true") === "false" ||
      Notification.permission !== "granted" ||
      document.visibilityState !== "visible"
    )
      return;
    await transport.client.POST(
      "/api/push/subscriptions/{subscription_id}/activity",
      {
        signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]),
        params: { path: { subscription_id: id } },
      },
    );
  });
}

export async function testPush(transport: Transport): Promise<void> {
  const response = await result(
    transport.client.POST("/api/push/subscriptions/{subscription_id}/test", {
      signal: AbortSignal.timeout(30000),
      params: { path: { subscription_id: pushSubscriptionId() } },
    }),
  );
  if (!response.accepted)
    throw new Error(
      "The push service did not accept the test. Try enabling background notifications again.",
    );
}

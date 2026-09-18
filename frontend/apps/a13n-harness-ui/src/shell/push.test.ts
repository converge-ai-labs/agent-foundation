// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createTransport } from "../transport/client";
import {
  disablePush,
  enablePush,
  pushSubscriptionId,
  reportPushActivity,
  testPush,
} from "./push";
import { writePreference } from "./preferences";

const key = "B" + "A".repeat(86);
let permission: NotificationPermission;
let current: PushSubscription | null;
let unsubscribe: ReturnType<typeof vi.fn>;
let subscribe: ReturnType<typeof vi.fn>;
let requestPermission: ReturnType<typeof vi.fn>;
let fetchMock: ReturnType<typeof vi.fn>;
let registration: ServiceWorkerRegistration;
let transport: ReturnType<typeof createTransport>;

beforeEach(() => {
  localStorage.clear();
  permission = "granted";
  unsubscribe = vi.fn(async () => {
    current = null;
    return true;
  });
  const device = {
    endpoint: "https://fcm.googleapis.com/fcm/send/fixture",
    options: {},
    unsubscribe,
    toJSON: () => ({ keys: { p256dh: "receiver-key", auth: "receiver-auth" } }),
  } as unknown as PushSubscription;
  current = null;
  subscribe = vi.fn(async () => {
    current = device;
    return device;
  });
  requestPermission = vi.fn(async () => {
    permission = "granted";
    return permission;
  });
  vi.stubGlobal("Notification", {
    get permission() {
      return permission;
    },
    requestPermission,
  });
  vi.stubGlobal("isSecureContext", true);
  vi.stubGlobal("PushManager", function () {});
  registration = {
    pushManager: { getSubscription: vi.fn(async () => current), subscribe },
    getNotifications: vi.fn(async () => []),
  } as unknown as ServiceWorkerRegistration;
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: {
      register: vi.fn(async () => registration),
      ready: Promise.resolve(registration),
      getRegistration: vi.fn(async () => registration),
    },
  });
  let tail = Promise.resolve<unknown>(undefined);
  Object.defineProperty(navigator, "locks", {
    configurable: true,
    value: {
      request: vi.fn((_name, callback) => {
        const next = tail.then(callback);
        tail = next.catch(() => {});
        return next;
      }),
    },
  });
  fetchMock = vi.fn(async (request: Request) => {
    const path = new URL(request.url).pathname;
    if (request.method === "DELETE") return new Response(null, { status: 204 });
    if (path.endsWith("configuration"))
      return Response.json({ public_key: key });
    if (path.endsWith("/test")) return Response.json({ accepted: true });
    return Response.json({ subscription_id: "subscription-one" });
  });
  vi.stubGlobal("fetch", fetchMock);
  transport = createTransport("test-key", vi.fn());
});
afterEach(() => {
  transport.close();
  Reflect.deleteProperty(navigator, "serviceWorker");
  Reflect.deleteProperty(navigator, "locks");
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("requires an explicit opt-in, then registers a root worker and persists the authenticated subscription", async () => {
  permission = "default";
  await enablePush(transport);
  expect(requestPermission).toHaveBeenCalledOnce();
  expect(requestPermission.mock.invocationCallOrder[0]).toBeLessThan(
    fetchMock.mock.invocationCallOrder[0],
  );
  expect(navigator.serviceWorker.register).toHaveBeenCalledWith("/sw.js", {
    scope: "/",
    updateViaCache: "none",
  });
  expect(subscribe).toHaveBeenCalledWith({
    userVisibleOnly: true,
    applicationServerKey: expect.any(Uint8Array),
  });
  const put = fetchMock.mock.calls
    .map(([request]) => request as Request)
    .find((request) => request.method === "PUT")!;
  expect(put.headers.get("authorization")).toBe("Bearer test-key");
  expect(await put.json()).toMatchObject({
    origin: window.location.origin,
  });
  expect(pushSubscriptionId()).toBe("subscription-one");
});

it("does not prompt or subscribe merely on restore, and reuses a tracked browser subscription", async () => {
  await enablePush(transport, false);
  expect(fetchMock).not.toHaveBeenCalled();
  await enablePush(transport);
  await enablePush(transport, false);
  expect(subscribe).toHaveBeenCalledOnce();
  expect(requestPermission).not.toHaveBeenCalled();
});

it("invalidates a newly created subscription after a lost save response", async () => {
  fetchMock.mockImplementation(async (request: Request) => {
    if (request.method === "PUT")
      throw new TypeError("connection lost after save");
    return Response.json({ public_key: key });
  });
  await expect(enablePush(transport)).rejects.toThrow("Unable to reach");
  expect(unsubscribe).toHaveBeenCalledOnce();
  expect(pushSubscriptionId()).toBe("");
});

it("keeps a tracked subscription through a transient refresh failure", async () => {
  await enablePush(transport);
  fetchMock.mockImplementation(async (request: Request) => {
    if (request.method === "PUT") return new Response(null, { status: 503 });
    return Response.json({ public_key: key });
  });
  await expect(enablePush(transport, false)).rejects.toThrow();
  expect(unsubscribe).not.toHaveBeenCalled();
  expect(pushSubscriptionId()).toBe("subscription-one");
});

it("rolls back opt-in when browser storage is unavailable", async () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("storage blocked");
  });
  await expect(enablePush(transport)).rejects.toThrow(
    "Browser storage is unavailable",
  );
  expect(unsubscribe).toHaveBeenCalled();
  expect(
    fetchMock.mock.calls.some(([request]) => request.method === "DELETE"),
  ).toBe(true);
});

it("disables locally despite an unreachable server and prevents a queued refresh from re-enabling", async () => {
  await enablePush(transport);
  fetchMock.mockRejectedValue(new TypeError("offline"));
  await Promise.all([disablePush(transport), enablePush(transport, false)]);
  expect(unsubscribe).toHaveBeenCalledOnce();
  expect(pushSubscriptionId()).toBe("");
  expect(subscribe).toHaveBeenCalledOnce();
});

it("still removes server state when browser unsubscribe fails, but reports failure when both fail", async () => {
  await enablePush(transport);
  unsubscribe.mockRejectedValue(new Error("browser failed"));
  await disablePush(transport);
  expect(pushSubscriptionId()).toBe("");
  writePreference("notifications.push-subscription", "subscription-one");
  fetchMock.mockRejectedValue(new TypeError("offline"));
  await expect(disablePush(transport)).rejects.toThrow("Could not disable");
  expect(pushSubscriptionId()).toBe("subscription-one");
});

it("does not create a subscription after reminders were disabled during permission approval", async () => {
  permission = "default";
  requestPermission.mockImplementation(async () => {
    writePreference("notifications.enabled", "false");
    permission = "granted";
    return permission;
  });
  await enablePush(transport);
  expect(subscribe).not.toHaveBeenCalled();
});

it("reports provider acceptance without claiming device delivery", async () => {
  await enablePush(transport);
  await testPush(transport);
  fetchMock.mockResolvedValue(Response.json({ accepted: false }));
  await expect(testPush(transport)).rejects.toThrow("did not accept");
});

it("requires explicit reconnection when a tracked browser subscription has disappeared", async () => {
  await enablePush(transport);
  current = null;
  await expect(enablePush(transport, false)).rejects.toThrow("expired");
  expect(subscribe).toHaveBeenCalledOnce();
  expect(pushSubscriptionId()).toBe("subscription-one");
  await enablePush(transport);
  expect(subscribe).toHaveBeenCalledTimes(2);
});

it("does not silently replace a subscription when the server signing key changes", async () => {
  await enablePush(transport);
  Object.assign(current!.options, {
    applicationServerKey: new Uint8Array([1]).buffer,
  });
  await expect(enablePush(transport, false)).rejects.toThrow("expired");
  expect(unsubscribe).not.toHaveBeenCalled();
  expect(subscribe).toHaveBeenCalledOnce();
  await enablePush(transport);
  expect(unsubscribe).toHaveBeenCalledOnce();
  expect(subscribe).toHaveBeenCalledTimes(2);
});

it("replaces a rejected endpoint on explicit reconnect even if the browser still returns it", async () => {
  await enablePush(transport);
  await enablePush(transport);
  expect(unsubscribe).toHaveBeenCalledOnce();
  expect(subscribe).toHaveBeenCalledTimes(2);
});

it("identifies a device push-service registration failure without saving or retrying", async () => {
  const cause = new DOMException(
    "Registration failed - push service error",
    "AbortError",
  );
  subscribe.mockRejectedValue(cause);
  await expect(enablePush(transport)).rejects.toMatchObject({
    message: expect.stringContaining(
      "Browser push registration failed before a subscription could be saved to Harness UI",
    ),
    cause,
  });
  expect(subscribe).toHaveBeenCalledOnce();
  expect(
    fetchMock.mock.calls.some(([request]) => request.method === "PUT"),
  ).toBe(false);
  expect(pushSubscriptionId()).toBe("");
});

it("preserves permission errors as browser registration failures, not server send errors", async () => {
  subscribe.mockRejectedValue(
    new DOMException("Permission denied", "NotAllowedError"),
  );
  await expect(enablePush(transport)).rejects.toThrow(
    "Check this site's notification permission",
  );
  expect(subscribe).toHaveBeenCalledOnce();
  expect(pushSubscriptionId()).toBe("");
});

it("reports only existing visible opt-ins without registering a worker or refreshing keys", async () => {
  const signal = new AbortController().signal;
  await reportPushActivity(transport, signal);
  expect(fetchMock).not.toHaveBeenCalled();
  writePreference("notifications.push-subscription", "subscription-one");
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
  await reportPushActivity(transport, signal);
  expect(fetchMock).toHaveBeenCalledOnce();
  const sent = fetchMock.mock.calls[0][0] as Request;
  expect(new URL(sent.url).pathname).toBe(
    "/api/push/subscriptions/subscription-one/activity",
  );
  expect(sent.method).toBe("POST");
  expect(sent.headers.has("authorization")).toBe(true);
  expect(navigator.serviceWorker.register).not.toHaveBeenCalled();
  expect(subscribe).not.toHaveBeenCalled();
});

it.each(["hidden", "disabled", "revoked", "removed", "aborted"])(
  "rechecks %s after waiting for the subscription mutation lock",
  async (change) => {
    writePreference("notifications.push-subscription", "subscription-one");
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
    let release!: () => Promise<void>;
    Object.defineProperty(navigator, "locks", {
      configurable: true,
      value: {
        request: (_name: string, callback: () => Promise<void>) =>
          new Promise<void>((resolve, reject) => {
            release = async () => {
              try {
                await callback();
                resolve();
              } catch (error) {
                reject(error);
              }
            };
          }),
      },
    });
    const controller = new AbortController();
    const pending = reportPushActivity(transport, controller.signal);
    if (change === "hidden")
      vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    if (change === "disabled")
      writePreference("notifications.enabled", "false");
    if (change === "revoked") permission = "denied";
    if (change === "removed")
      writePreference("notifications.push-subscription", "");
    if (change === "aborted") controller.abort();
    await release();
    await pending;
    expect(fetchMock).not.toHaveBeenCalled();
    expect(subscribe).not.toHaveBeenCalled();
  },
);

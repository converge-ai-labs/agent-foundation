// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  deliverOnce,
  NotificationSettings,
  notificationPermission,
  NotificationsProvider,
  useNotifications,
} from "./notifications";
import {
  createTransport,
  type Schema,
  type Transport,
} from "../transport/client";
import { TransportContext } from "../transport/context";
import * as push from "./push";
import { writePreference } from "./preferences";

let permission: NotificationPermission;
let request: ReturnType<typeof vi.fn>;
let native: ReturnType<typeof vi.fn>;
let queries: QueryClient;
const notice = (
  receipt = "receipt-1",
  status: "completed" | "failed" | "suspended" = "completed",
): Schema<"SummaryInvalidation"> => ({
  epoch: "epoch-1",
  sequence: 1,
  kind: "root_operation",
  root_thread_id: "thread-1",
  thread_id: "thread-1",
  notice: {
    receipt_id: receipt,
    status,
    brief:
      status === "suspended"
        ? "Which project should I update?"
        : "Fixed project expansion and archived filtering.",
  },
});
let event = notice();
beforeEach(() => {
  localStorage.clear();
  permission = "default";
  event = notice();
  request = vi.fn(async () => {
    permission = "granted";
    return permission;
  });
  native = vi.fn(function () {
    return { close: vi.fn(), onclick: null, onclose: null };
  });
  Object.defineProperty(native, "permission", { get: () => permission });
  Object.assign(native, { requestPermission: request });
  vi.stubGlobal("Notification", native);
  vi.stubGlobal("isSecureContext", true);
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  vi.spyOn(document, "hasFocus").mockReturnValue(false);
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  queries = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  queries.setQueryData(["thread", "thread-1", "detail"], {
    thread: { title: "UI polish" },
  });
});
afterEach(() => {
  cleanup();
  queries.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
function Controls() {
  const notifications = useNotifications()!;
  const location = useLocation();
  return (
    <>
      <button onClick={() => notifications.receive(event)}>Emit</button>
      <output>{location.pathname}</output>
      <NotificationSettings />
    </>
  );
}
function mount(path = "/threads/thread-1", transport: Transport | null = null) {
  return render(
    <QueryClientProvider client={queries}>
      <TransportContext.Provider value={transport}>
        <MemoryRouter initialEntries={[path]}>
          <NotificationsProvider>
            <Controls />
          </NotificationsProvider>
        </MemoryRouter>
      </TransportContext.Provider>
    </QueryClientProvider>,
  );
}
it("requires explicit background opt-in for granted permission and suppresses page-native duplicates", async () => {
  permission = "granted";
  const transport = createTransport("fixture-key", vi.fn());
  vi.spyOn(push, "supportsPush").mockReturnValue(true);
  const enable = vi.spyOn(push, "enablePush").mockImplementation(async () => {
    writePreference("notifications.push-subscription", "subscription-one");
  });
  const test = vi.spyOn(push, "testPush").mockResolvedValue();
  vi.spyOn(push, "disablePush").mockImplementation(async () => {
    writePreference("notifications.push-subscription", "");
  });
  mount("/threads/thread-1", transport);
  expect(enable).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole("button", { name: "Enable background notifications" }),
  );
  await screen.findByText("Enabled on this device");
  expect(enable).toHaveBeenCalledWith(transport, ["thread-1"]);
  fireEvent.click(screen.getByText("Emit"));
  expect(native).not.toHaveBeenCalled();
  fireEvent.click(
    screen.getByRole("button", { name: "Send test notification" }),
  );
  await screen.findByText(/push service accepted the test/);
  expect(test).toHaveBeenCalledWith(transport);
  fireEvent.click(
    screen.getByRole("switch", { name: "Enable browser notifications" }),
  );
  await screen.findByText("Not enabled");
  expect(push.disablePush).toHaveBeenCalled();
  transport.close();
});

it("schedules cleanup even when a first opt-in has not returned its subscription ID", async () => {
  permission = "granted";
  const transport = createTransport("fixture-key", vi.fn());
  vi.spyOn(push, "supportsPush").mockReturnValue(true);
  let finish!: () => void;
  vi.spyOn(push, "enablePush").mockImplementation(
    () =>
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
  );
  const disable = vi.spyOn(push, "disablePush").mockResolvedValue();
  mount("/threads/thread-1", transport);
  fireEvent.click(
    screen.getByRole("button", { name: "Enable background notifications" }),
  );
  fireEvent.click(
    screen.getByRole("switch", { name: "Enable browser notifications" }),
  );
  expect(disable).toHaveBeenCalledWith(transport);
  await act(async () => finish());
  transport.close();
});

it("keeps a single permission prompt until explicit permission or disabling, without automatic browser requests", async () => {
  const first = mount();
  expect(screen.getAllByLabelText("Notification permission")).toHaveLength(1);
  expect(request).not.toHaveBeenCalled();
  await act(async () => {
    fireEvent.click(
      screen.getByRole("button", { name: "Enable notifications" }),
    );
  });
  expect(request).toHaveBeenCalledOnce();
  expect(screen.queryByLabelText("Notification permission")).toBeNull();
  expect(screen.getByText("Allowed")).toBeTruthy();
  fireEvent.click(
    screen.getByRole("button", { name: "Send test notification" }),
  );
  expect(native).toHaveBeenCalledOnce();
  fireEvent.click(
    screen.getByRole("switch", { name: "Enable browser notifications" }),
  );
  expect(localStorage.getItem("a13n-harness-ui.notifications.enabled")).toBe(
    "false",
  );
  expect(native.mock.results[0].value.close).toHaveBeenCalled();
  first.unmount();
  mount();
  expect(screen.queryByLabelText("Notification permission")).toBeNull();
  expect(
    screen
      .getByRole("button", { name: "Send test notification" })
      .hasAttribute("disabled"),
  ).toBe(true);
});
it("retains the prompt after dismissing browser permission, and permits re-enabling from Settings", async () => {
  request.mockResolvedValue("default");
  mount();
  await act(async () => {
    fireEvent.click(
      screen.getByRole("button", { name: "Enable notifications" }),
    );
  });
  expect(screen.getAllByLabelText("Notification permission")).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Turn off reminders" }));
  expect(screen.queryByLabelText("Notification permission")).toBeNull();
  fireEvent.click(
    screen.getByRole("switch", { name: "Enable browser notifications" }),
  );
  expect(screen.getAllByLabelText("Notification permission")).toHaveLength(1);
});
it("explains denied and unavailable permission instead of repeatedly requesting it", () => {
  permission = "denied";
  mount();
  expect(screen.getByText("Blocked by browser")).toBeTruthy();
  expect(
    screen.queryByRole("button", { name: "Enable notifications" }),
  ).toBeNull();
  expect(request).not.toHaveBeenCalled();
  vi.stubGlobal("isSecureContext", false);
  fireEvent(document, new Event("visibilitychange"));
  expect(screen.getByText("Unavailable here")).toBeTruthy();
});
it("renders actual briefs, deduplicates replay, and opens the matching conversation from a native notification", async () => {
  permission = "granted";
  localStorage.setItem(
    "a13n-harness-ui.notifications.conversations",
    '["thread-1"]',
  );
  mount("/settings/notifications");
  fireEvent.click(screen.getByText("Emit"));
  await waitFor(() => expect(native).toHaveBeenCalledOnce());
  expect(native).toHaveBeenCalledWith(
    "Task completed · UI polish",
    expect.objectContaining({
      body: event.notice!.brief,
      tag: "a13n-harness-ui.receipt-1",
    }),
  );
  expect(await screen.findByText(event.notice!.brief)).toBeTruthy();
  fireEvent.click(screen.getByText("Emit"));
  expect(native).toHaveBeenCalledOnce();
  vi.spyOn(window, "focus").mockImplementation(() => {});
  act(() => native.mock.results[0].value.onclick());
  expect(screen.getByText("/threads/thread-1")).toBeTruthy();
});
it("keeps attention notices without permission or when desktop reminders are disabled", async () => {
  mount();
  permission = "default";
  fireEvent(document, new Event("visibilitychange"));
  event = notice("receipt-2", "suspended");
  fireEvent.click(screen.getByText("Emit"));
  expect(
    await screen.findByText("Which project should I update?"),
  ).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Turn off reminders" }));
  permission = "granted";
  vi.mocked(document.hasFocus).mockReturnValue(false);
  event = notice("receipt-3", "failed");
  fireEvent.click(screen.getByText("Emit"));
  expect(native).not.toHaveBeenCalled();
});
it.each([
  {
    focused: true,
    visibility: "visible",
    current: true,
    desktop: false,
    banner: false,
  },
  {
    focused: true,
    visibility: "visible",
    current: false,
    desktop: false,
    banner: true,
  },
  {
    focused: false,
    visibility: "visible",
    current: true,
    desktop: true,
    banner: false,
  },
  {
    focused: false,
    visibility: "visible",
    current: false,
    desktop: true,
    banner: true,
  },
  {
    focused: true,
    visibility: "hidden",
    current: true,
    desktop: true,
    banner: false,
  },
] as const)(
  "routes completion feedback by attention: %j",
  async ({ focused, visibility, current, desktop, banner }) => {
    permission = "granted";
    vi.mocked(document.hasFocus).mockReturnValue(focused);
    vi.spyOn(document, "visibilityState", "get").mockReturnValue(visibility);
    localStorage.setItem(
      "a13n-harness-ui.notifications.conversations",
      '["thread-1"]',
    );
    mount(current ? "/threads/thread-1" : "/threads/thread-2");
    await act(async () => fireEvent.click(screen.getByText("Emit")));
    expect(native).toHaveBeenCalledTimes(desktop ? 1 : 0);
    expect(!!screen.queryByText(event.notice!.brief)).toBe(banner);
  },
);
it.each(["failed", "suspended"] as const)(
  "keeps %s banners in the focused conversation without a desktop alert",
  async (status) => {
    permission = "granted";
    vi.mocked(document.hasFocus).mockReturnValue(true);
    event = notice("receipt-1", status);
    mount();
    await act(async () => fireEvent.click(screen.getByText("Emit")));
    expect(screen.getByText(event.notice!.brief)).toBeTruthy();
    expect(native).not.toHaveBeenCalled();
  },
);
it("rechecks focus after waiting for the native delivery lock", async () => {
  permission = "granted";
  let claim!: () => void;
  vi.stubGlobal("navigator", {
    locks: {
      request: vi.fn(async (_name, callback) => {
        claim = callback;
      }),
    },
  });
  mount();
  fireEvent.click(screen.getByText("Emit"));
  vi.mocked(document.hasFocus).mockReturnValue(true);
  await act(async () => claim());
  expect(native).not.toHaveBeenCalled();
});
it("does not synthesize historical notices or notify for unopened conversations", () => {
  permission = "granted";
  mount("/settings/notifications");
  expect(native).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Emit"));
  expect(native).not.toHaveBeenCalled();
  expect(screen.queryByText(event.notice!.brief)).toBeNull();
});
it("reflects permission revocation and another tab disabling reminders", () => {
  permission = "granted";
  mount();
  permission = "denied";
  fireEvent(document, new Event("visibilitychange"));
  expect(screen.getByText("Blocked by browser")).toBeTruthy();
  localStorage.setItem("a13n-harness-ui.notifications.enabled", "false");
  fireEvent(
    window,
    new StorageEvent("storage", {
      key: "a13n-harness-ui.notifications.enabled",
    }),
  );
  expect(screen.queryByLabelText("Notification permission")).toBeNull();
});
it("serializes concurrent tab delivery and does not claim failed delivery", async () => {
  let tail = Promise.resolve();
  const locks = {
    request: vi.fn((_name: string, callback: () => void) => {
      tail = tail.then(callback);
      return tail;
    }),
  };
  vi.stubGlobal("navigator", { locks });
  const show = vi.fn(() => true);
  await Promise.all([deliverOnce("same", show), deliverOnce("same", show)]);
  expect(show).toHaveBeenCalledOnce();
  await deliverOnce("retry", () => false);
  await deliverOnce("retry", show);
  expect(show).toHaveBeenCalledTimes(2);
});
it("continues in-app delivery when native notification construction fails", async () => {
  permission = "granted";
  native.mockImplementation(function () {
    throw new Error("Unsupported constructor");
  });
  event = notice("receipt-1", "failed");
  mount();
  fireEvent.click(screen.getByText("Emit"));
  expect(await screen.findByText(event.notice!.brief)).toBeTruthy();
  expect(screen.getAllByRole("alert")[0].textContent).toContain(
    "could not display",
  );
});

it("allows foreground test notifications without claiming delivery and reports asynchronous failures", async () => {
  permission = "granted";
  vi.mocked(document.hasFocus).mockReturnValue(true);
  mount();
  fireEvent.click(
    screen.getByRole("button", { name: "Send test notification" }),
  );
  expect(native).toHaveBeenCalledOnce();
  expect(
    screen.getByText(/Test requested from the browser/).textContent,
  ).toContain("does not confirm");
  const notification = native.mock.results[0].value;
  act(() => notification.onshow());
  expect(screen.getByText(/browser reported/)).toBeTruthy();
  act(() => notification.onerror());
  expect(screen.getAllByRole("alert")[0].textContent).toContain(
    "system notification settings",
  );
  expect(screen.queryByText(/browser reported/)).toBeNull();
});
it("falls back to native delivery when optional cross-tab locking is unavailable", async () => {
  permission = "granted";
  vi.stubGlobal("navigator", {
    locks: {
      request: vi.fn().mockRejectedValue(new Error("Lock unavailable")),
    },
  });
  mount();
  fireEvent.click(screen.getByText("Emit"));
  await waitFor(() => expect(native).toHaveBeenCalledOnce());
  expect(screen.queryByText(event.notice!.brief)).toBeNull();
});

function worker() {
  const close = vi.fn();
  const showNotification = vi.fn(
    async (_title: string, _options: NotificationOptions) => {},
  );
  const getNotifications = vi.fn(
    async (_filter?: GetNotificationOptions) => [] as Notification[],
  );
  const registration = {
    showNotification,
    getNotifications,
  } as unknown as ServiceWorkerRegistration;
  vi.stubGlobal("navigator", { serviceWorker: {} });
  vi.spyOn(push, "notificationWorker").mockResolvedValue(registration);
  return { registration, showNotification, getNotifications, close };
}

it("uses worker notifications on Android for live alerts and foreground tests without a push subscription", async () => {
  permission = "granted";
  native.mockImplementation(function () {
    throw new TypeError("Illegal constructor");
  });
  const sw = worker();
  mount();
  fireEvent.click(screen.getByText("Emit"));
  await waitFor(() => expect(sw.showNotification).toHaveBeenCalledOnce());
  expect(sw.showNotification).toHaveBeenCalledWith(
    "Task completed · UI polish",
    expect.objectContaining({
      tag: "a13n-harness-ui.receipt-1",
      data: { path: "/threads/thread-1" },
    }),
  );
  vi.mocked(document.hasFocus).mockReturnValue(true);
  fireEvent.click(
    screen.getByRole("button", { name: "Send test notification" }),
  );
  await screen.findByText(/browser accepted the test notification/);
  expect(sw.showNotification).toHaveBeenCalledTimes(2);
  expect(native).not.toHaveBeenCalled();
  expect(push.pushSubscriptionId()).toBe("");
});

it("does not suppress live worker alerts when an enabled push provider silently fails", async () => {
  permission = "granted";
  const sw = worker();
  writePreference("notifications.push-subscription", "subscription-one");
  const transport = createTransport("fixture-key", vi.fn());
  vi.spyOn(push, "enablePush").mockResolvedValue();
  mount("/threads/thread-1", transport);
  await screen.findByText("Enabled on this device");
  fireEvent.click(screen.getByText("Emit"));
  await waitFor(() => expect(sw.showNotification).toHaveBeenCalledOnce());
  transport.close();
});

it("reuses an already displayed push with the same receipt tag", async () => {
  permission = "granted";
  const sw = worker();
  sw.getNotifications.mockResolvedValue([
    { close: sw.close } as unknown as Notification,
  ]);
  mount();
  await act(async () => fireEvent.click(screen.getByText("Emit")));
  expect(sw.getNotifications).toHaveBeenCalledWith({
    tag: "a13n-harness-ui.receipt-1",
  });
  expect(sw.showNotification).not.toHaveBeenCalled();
});

it.each(["disable", "focus", "unmount"] as const)(
  "rechecks %s after waiting for the worker",
  async (change) => {
    permission = "granted";
    const sw = worker();
    let ready!: (registration: ServiceWorkerRegistration) => void;
    vi.mocked(push.notificationWorker).mockReturnValue(
      new Promise((resolve) => {
        ready = resolve;
      }),
    );
    const view = mount();
    fireEvent.click(screen.getByText("Emit"));
    if (change === "disable")
      fireEvent.click(
        screen.getByRole("switch", { name: "Enable browser notifications" }),
      );
    else if (change === "focus")
      vi.mocked(document.hasFocus).mockReturnValue(true);
    else view.unmount();
    await act(async () => ready(sw.registration));
    expect(sw.showNotification).not.toHaveBeenCalled();
  },
);

it("retains a failed subscription as reconnect intent, not enabled delivery, and retries on reconnect", async () => {
  permission = "granted";
  const sw = worker();
  writePreference("notifications.push-subscription", "stale-subscription");
  const transport = createTransport("fixture-key", vi.fn());
  const enable = vi
    .spyOn(push, "enablePush")
    .mockRejectedValue(new Error("offline"));
  mount("/threads/thread-1", transport);
  expect(screen.getByText("Checking subscription…")).toBeTruthy();
  expect(screen.queryByText("Enabled on this device")).toBeNull();
  await screen.findByText("Subscription needs attention");
  expect(push.pushSubscriptionId()).toBe("stale-subscription");
  fireEvent.click(screen.getByText("Emit"));
  await waitFor(() => expect(sw.showNotification).toHaveBeenCalledOnce());
  enable.mockResolvedValue();
  fireEvent(window, new Event("online"));
  await screen.findByText("Enabled on this device");
  expect(enable).toHaveBeenCalledTimes(2);
  expect(screen.queryAllByRole("alert")).toHaveLength(0);
  transport.close();
});

it("marks a rejected background test as needing attention instead of claiming enabled delivery", async () => {
  permission = "granted";
  writePreference("notifications.push-subscription", "subscription-one");
  const transport = createTransport("fixture-key", vi.fn());
  vi.spyOn(push, "enablePush").mockResolvedValue();
  vi.spyOn(push, "testPush").mockRejectedValue(
    new Error("Reconnect background notifications"),
  );
  mount("/settings/notifications", transport);
  await screen.findByText("Enabled on this device");
  fireEvent.click(
    screen.getByRole("button", { name: "Send test notification" }),
  );
  await screen.findByText("Subscription needs attention");
  expect(screen.getByRole("alert").textContent).toContain("Reconnect");
  transport.close();
});

it.each([
  {
    userAgent: "Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) CriOS/130",
    platform: "iPad",
    maxTouchPoints: 5,
  },
  {
    userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15) CriOS/130",
    platform: "MacIntel",
    maxTouchPoints: 5,
  },
])(
  "explains Home Screen installation in iPad Chrome including desktop mode: %j",
  (device) => {
    vi.stubGlobal("navigator", device);
    vi.stubGlobal("Notification", undefined);
    mount("/settings/notifications");
    expect(screen.getByText("Open from Home Screen")).toBeTruthy();
    expect(
      screen.getAllByText(/Use Share → Add to Home Screen/).length,
    ).toBeGreaterThan(0);
    expect(
      screen.queryByRole("button", { name: "Allow notifications" }),
    ).toBeNull();
    expect(
      screen
        .getByRole("button", { name: "Send test notification" })
        .hasAttribute("disabled"),
    ).toBe(true);
    expect(request).not.toHaveBeenCalled();
  },
);

it("uses real capabilities in the installed iPad app without blocking permission on a platform hint", async () => {
  vi.stubGlobal("navigator", {
    userAgent: "iPad",
    standalone: true,
    serviceWorker: {},
  });
  vi.stubGlobal("PushManager", function () {});
  expect(notificationPermission()).toBe("default");
  const transport = createTransport("fixture-key", vi.fn());
  const enable = vi.spyOn(push, "enablePush").mockImplementation(async () => {
    permission = "granted";
    writePreference("notifications.push-subscription", "ipad-subscription");
  });
  mount("/settings/notifications", transport);
  expect(enable).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Allow notifications" }));
  await screen.findByText("Enabled on this device");
  expect(enable).toHaveBeenCalledWith(transport, []);
  transport.close();
});

it("holds cross-tab delivery claims until worker display completes, and does not claim rejected delivery", async () => {
  let tail = Promise.resolve();
  vi.stubGlobal("navigator", {
    locks: {
      request: vi.fn((_name, callback) => {
        tail = tail.then(callback);
        return tail;
      }),
    },
  });
  let complete!: (accepted: boolean) => void;
  const show = vi.fn(
    () =>
      new Promise<boolean>((resolve) => {
        complete = resolve;
      }),
  );
  const first = deliverOnce("worker-receipt", show);
  const second = deliverOnce("worker-receipt", show);
  await waitFor(() => expect(show).toHaveBeenCalledOnce());
  complete(true);
  await Promise.all([first, second]);
  expect(show).toHaveBeenCalledOnce();
  await deliverOnce("worker-failed", async () => false);
  const retry = vi.fn(async () => true);
  await deliverOnce("worker-failed", retry);
  expect(retry).toHaveBeenCalledOnce();
});

it("sends a new local test each time even while the previous test remains in the notification center", async () => {
  permission = "granted";
  const sw = worker();
  const displayed = new Set<string>();
  sw.showNotification.mockImplementation(async (_title, options) => {
    displayed.add(options.tag!);
  });
  sw.getNotifications.mockImplementation(async (filter) =>
    displayed.has(filter?.tag ?? "")
      ? [{ close: sw.close } as unknown as Notification]
      : [],
  );
  mount("/settings/notifications");
  for (let attempt = 1; attempt <= 2; attempt++) {
    fireEvent.click(
      screen.getByRole("button", { name: "Send test notification" }),
    );
    await screen.findByText(/browser accepted the test notification/);
    expect(sw.showNotification).toHaveBeenCalledTimes(attempt);
  }
  expect(displayed.size).toBe(2);
});

it.each([
  { userAgent: "iPad", standalone: true, platform: "iPad", maxTouchPoints: 5 },
  { userAgent: "Macintosh", platform: "MacIntel", maxTouchPoints: 0 },
])(
  "does not prescribe installation for an already installed app or ordinary Mac: %j",
  (device) => {
    vi.stubGlobal("navigator", device);
    vi.stubGlobal("Notification", undefined);
    expect(notificationPermission()).toBe("unavailable");
  },
);

it("keeps installation guidance inline without duplicating a persistent toast in notification settings", () => {
  vi.stubGlobal("navigator", {
    userAgent: "iPad",
    platform: "iPad",
    maxTouchPoints: 5,
  });
  vi.stubGlobal("Notification", undefined);
  mount("/settings/notifications");
  expect(screen.getAllByText(/Use Share → Add to Home Screen/)).toHaveLength(1);
  expect(screen.queryByLabelText("Notification permission")).toBeNull();
  expect(
    screen.queryByText(/Without background delivery, keep WebUI open/),
  ).toBeNull();
});

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
  vi.stubGlobal("PushManager", function () {});
  Object.defineProperty(navigator, "serviceWorker", {
    configurable: true,
    value: {},
  });
  vi.spyOn(push, "reportPushActivity").mockResolvedValue();
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
  Reflect.deleteProperty(navigator, "serviceWorker");
  vi.useRealTimers();
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
  expect(enable).toHaveBeenCalledWith(transport);
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
  expect(screen.queryByLabelText("Notification permission")).toBeNull();
  fireEvent.click(
    screen.getByRole("switch", { name: "Enable browser notifications" }),
  );
  expect(disable).toHaveBeenCalledWith(transport);
  await act(async () => finish());
  transport.close();
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
it("retains a failed subscription as reconnect intent, not enabled delivery, and retries on reconnect", async () => {
  permission = "granted";
  writePreference("notifications.push-subscription", "stale-subscription");
  const transport = createTransport("fixture-key", vi.fn());
  vi.spyOn(push, "supportsPush").mockReturnValue(true);
  const enable = vi
    .spyOn(push, "enablePush")
    .mockRejectedValue(new Error("offline"));
  mount("/threads/thread-1", transport);
  expect(screen.getByText("Checking subscription…")).toBeTruthy();
  expect(screen.queryByLabelText("Notification permission")).toBeNull();
  expect(screen.queryByText("Enabled on this device")).toBeNull();
  await screen.findByText("Subscription needs attention");
  expect(push.pushSubscriptionId()).toBe("stale-subscription");
  fireEvent.click(screen.getByText("Emit"));
  expect(await screen.findByText(event.notice!.brief)).toBeTruthy();
  expect(native).not.toHaveBeenCalled();
  expect(
    screen.getByRole("heading", { name: "Reconnect background notifications" }),
  ).toBeTruthy();
  let finish!: () => void;
  enable.mockImplementation(
    () =>
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
  );
  fireEvent(window, new Event("online"));
  expect(screen.getByText("Checking subscription…")).toBeTruthy();
  // The previous error is not proof that this in-flight reconciliation failed.
  expect(screen.queryByLabelText("Notification permission")).toBeNull();
  await act(async () => finish());
  await screen.findByText("Enabled on this device");
  expect(screen.queryByLabelText("Notification permission")).toBeNull();
  expect(enable).toHaveBeenCalledTimes(2);
  expect(screen.queryByText(/could not be synchronized/)).toBeNull();
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
  expect(enable).toHaveBeenCalledWith(transport);
  transport.close();
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

it.each(["completed", "failed", "suspended"] as const)(
  "shows %s on current and never-visited conversations regardless of focus, permission or opt-in",
  async (status) => {
    writePreference("notifications.enabled", "false");
    vi.mocked(document.hasFocus).mockReturnValue(true);
    event = notice("current", status);
    mount();
    fireEvent.click(screen.getByText("Emit"));
    expect(await screen.findByText(event.notice!.brief)).toBeTruthy();
    fireEvent.click(screen.getByText("Emit"));
    expect(screen.getAllByText(event.notice!.brief)).toHaveLength(1);
    event = { ...notice("unvisited", status), root_thread_id: "never-visited" };
    fireEvent.click(screen.getByText("Emit"));
    expect(screen.getAllByText(event.notice!.brief)).toHaveLength(2);
    fireEvent.click(
      screen.getAllByRole("button", { name: "Open conversation" })[0],
    );
    expect(await screen.findByText("/threads/never-visited")).toBeTruthy();
    expect(native).not.toHaveBeenCalled();
  },
);

it("does not synthesize history and deduplicates receipts only within an epoch", () => {
  mount("/settings/notifications");
  expect(screen.queryByText(event.notice!.brief)).toBeNull();
  fireEvent.click(screen.getByText("Emit"));
  fireEvent.click(screen.getByText("Emit"));
  expect(screen.getAllByText(event.notice!.brief)).toHaveLength(1);
  event = { ...event, epoch: "epoch-2" };
  fireEvent.click(screen.getByText("Emit"));
  expect(screen.getAllByText(event.notice!.brief)).toHaveLength(2);
});

it("has no permission-only setup or local test fallback without Push support", () => {
  vi.spyOn(push, "supportsPush").mockReturnValue(false);
  permission = "granted";
  mount();
  expect(screen.getByText("Unavailable here")).toBeTruthy();
  expect(
    screen
      .getByRole("button", { name: "Send test notification" })
      .hasAttribute("disabled"),
  ).toBe(true);
  expect(
    screen.queryByRole("button", { name: "Allow notifications" }),
  ).toBeNull();
  fireEvent.click(screen.getByText("Emit"));
  expect(screen.getByText(event.notice!.brief)).toBeTruthy();
  expect(native).not.toHaveBeenCalled();
  expect(request).not.toHaveBeenCalled();
});

it("pulses visible pages immediately and every minute, coalesces waits, and stops on hide or unmount", async () => {
  permission = "granted";
  writePreference("notifications.push-subscription", "subscription-one");
  vi.spyOn(push, "enablePush").mockResolvedValue();
  const transport = createTransport("fixture-key", vi.fn());
  vi.useFakeTimers();
  const view = mount("/settings/notifications", transport);
  await act(async () => {});
  expect(screen.getByText("Enabled on this device")).toBeTruthy();
  const activity = vi.mocked(push.reportPushActivity);
  expect(activity).toHaveBeenCalled();
  activity.mockClear();
  await act(async () => vi.advanceTimersByTime(60000));
  expect(activity).toHaveBeenCalledOnce();
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
  fireEvent(document, new Event("visibilitychange"));
  await act(async () => vi.advanceTimersByTime(120000));
  expect(activity).toHaveBeenCalledOnce();
  vi.spyOn(document, "visibilityState", "get").mockReturnValue("visible");
  await act(async () => fireEvent(document, new Event("visibilitychange")));
  expect(activity.mock.calls.length).toBeGreaterThan(1);
  activity.mockClear();
  let finish!: () => void;
  activity.mockImplementation(
    () =>
      new Promise<void>((resolve) => {
        finish = resolve;
      }),
  );
  await act(async () => vi.advanceTimersByTime(60000));
  await act(async () => vi.advanceTimersByTime(120000));
  expect(activity).toHaveBeenCalledOnce();
  const signal = activity.mock.calls[0][1];
  view.unmount();
  expect(signal.aborted).toBe(true);
  await act(async () => finish());
  await act(async () => vi.advanceTimersByTime(120000));
  expect(activity).toHaveBeenCalledOnce();
  transport.close();
});

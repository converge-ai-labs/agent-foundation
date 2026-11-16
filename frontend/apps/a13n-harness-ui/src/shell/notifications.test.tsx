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
import { Link, MemoryRouter, useLocation } from "react-router";
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
import { i18n } from "../i18n";

let permission: NotificationPermission;
let request: ReturnType<typeof vi.fn>;
let native: ReturnType<typeof vi.fn>;
let queries: QueryClient;
const notice = (
  receipt = "receipt-1",
  status: Schema<"RootOperationNotice">["status"] = "completed",
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
      <Link to="/threads/thread-other">Other thread</Link>
      <Link to="/threads/thread-1">Current thread</Link>
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
it("updates the toast viewport accessible name when the language changes", async () => {
  await act(() => i18n.changeLanguage("en"));
  mount();
  expect(screen.getByRole("region", { name: "Notifications" })).toBeTruthy();
  await act(() => i18n.changeLanguage("zh-CN"));
  expect(screen.getByRole("region", { name: "通知" })).toBeTruthy();
  await act(() => i18n.changeLanguage("en"));
});
it.each(["completed", "failed", "suspended"] as const)(
  "opens Bot chat for a %s notification",
  async (status) => {
    mount("/threads/thread-other");
    event = {
      ...notice(`bot-${status}`, status),
      root_thread_id: "thread_bot",
      thread_id: "thread_bot",
    };
    fireEvent.click(screen.getByText("Emit"));
    fireEvent.click(await screen.findByRole("button", { name: "Open chat" }));
    expect(screen.getByText("/bot")).toBeTruthy();
  },
);

it("relocalizes a retained suspended notice without replaying it", async () => {
  await act(() => i18n.changeLanguage("en"));
  mount("/threads/thread-other");
  event = notice("retained", "suspended");
  fireEvent.click(screen.getByText("Emit"));
  const title = await screen.findByText("Your input is needed - UI polish");
  const action = screen.getByRole("button", { name: "Open chat" });
  try {
    await act(() => i18n.changeLanguage("zh-CN"));
    expect(screen.getByText("需要你的输入 - UI polish")).toBe(title);
    expect(screen.getByRole("button", { name: "打开聊天" })).toBe(action);
    expect(screen.getByText("Which project should I update?")).toBeTruthy();
    fireEvent.click(screen.getByText("Emit"));
    expect(screen.getAllByText("需要你的输入 - UI polish")).toHaveLength(1);
    fireEvent.click(action);
    expect(screen.getByText("/threads/thread-1")).toBeTruthy();
  } finally {
    await act(() => i18n.changeLanguage("en"));
  }
});

it("updates the permission prompt when the language changes without dismissing it", async () => {
  mount();
  expect(await screen.findByText("Get task notifications")).toBeTruthy();
  try {
    await act(async () => {
      await i18n.changeLanguage("zh-CN");
    });
    expect(await screen.findByText("获取任务通知")).toBeTruthy();
    expect(screen.getByText("在任务完成或需要你输入时接收通知。")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "设置" }));
    expect(screen.getByText("/settings/notifications")).toBeTruthy();
  } finally {
    await act(async () => {
      await i18n.changeLanguage("en");
    });
  }
});

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
  await screen.findByText(
    /Test accepted by the push service; delivery is not confirmed/,
  );
  expect(test).toHaveBeenCalledWith(transport);
  fireEvent.click(
    screen.getByRole("switch", { name: "Enable browser notifications" }),
  );
  await screen.findByText("Not enabled");
  expect(push.disablePush).toHaveBeenCalled();
  transport.close();
});

it("relocalizes retained test status and synchronizes language without resending the test", async () => {
  permission = "granted";
  const transport = createTransport("fixture-key", vi.fn());
  vi.spyOn(push, "supportsPush").mockReturnValue(true);
  const enable = vi.spyOn(push, "enablePush").mockImplementation(async () => {
    writePreference("notifications.push-subscription", "subscription-one");
  });
  const test = vi.spyOn(push, "testPush").mockResolvedValue();
  mount("/settings#notifications", transport);
  try {
    fireEvent.click(
      screen.getByRole("button", { name: "Enable background notifications" }),
    );
    await screen.findByText("Enabled on this device");
    fireEvent.click(
      screen.getByRole("button", { name: "Send test notification" }),
    );
    await screen.findByText(/Test accepted by the push service/);
    const subscriptions = enable.mock.calls.length;
    await act(async () => {
      await i18n.changeLanguage("zh-CN");
    });
    expect(screen.getByText(/推送服务已接受测试/)).toBeTruthy();
    expect(enable).toHaveBeenCalledTimes(subscriptions + 1);
    expect(enable).toHaveBeenLastCalledWith(transport, false);
    expect(test).toHaveBeenCalledTimes(1);
  } finally {
    await act(async () => {
      await i18n.changeLanguage("en");
    });
    transport.close();
  }
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
it("keeps other-thread attention notices without permission or when desktop reminders are disabled", async () => {
  mount("/threads/thread-other");
  permission = "default";
  fireEvent(document, new Event("visibilitychange"));
  event = notice("receipt-2", "suspended");
  fireEvent.click(screen.getByText("Emit"));
  expect(
    await screen.findByText("Which project should I update?"),
  ).toBeTruthy();
  fireEvent.click(
    screen.getByRole("switch", { name: "Enable browser notifications" }),
  );
  permission = "granted";
  vi.mocked(document.hasFocus).mockReturnValue(false);
  event = notice("receipt-3", "failed");
  fireEvent.click(screen.getByText("Emit"));
  expect(native).not.toHaveBeenCalled();
});
it("dismisses setup without disabling notifications and remembers the dismissal", async () => {
  const view = mount();
  fireEvent.click(screen.getByLabelText("Dismiss notification"));
  await waitFor(() =>
    expect(screen.queryByText("Get task notifications")).toBeNull(),
  );
  expect(
    screen
      .getByRole("switch", { name: "Enable browser notifications" })
      .getAttribute("aria-checked"),
  ).toBe("true");
  view.unmount();
  mount();
  expect(screen.queryByText("Get task notifications")).toBeNull();
  const toggle = screen.getByRole("switch", {
    name: "Enable browser notifications",
  });
  fireEvent.click(toggle);
  fireEvent.click(toggle);
  expect(screen.getByText("Get task notifications")).toBeTruthy();
});
it("routes setup to Settings without requesting permission or repeating the toast there", async () => {
  mount();
  fireEvent.click(screen.getByRole("button", { name: "Settings" }));
  expect(await screen.findByText("/settings/notifications")).toBeTruthy();
  await waitFor(() =>
    expect(screen.queryByText("Get task notifications")).toBeNull(),
  );
  expect(request).not.toHaveBeenCalled();
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
  mount("/threads/thread-other", transport);
  expect(screen.getByText("Checking subscription…")).toBeTruthy();
  expect(screen.queryByLabelText("Notification permission")).toBeNull();
  expect(screen.queryByText("Enabled on this device")).toBeNull();
  await screen.findByText("Subscription needs attention");
  expect(push.pushSubscriptionId()).toBe("stale-subscription");
  fireEvent.click(screen.getByText("Emit"));
  expect(await screen.findByText(event.notice!.brief)).toBeTruthy();
  expect(native).not.toHaveBeenCalled();
  expect(screen.getByText("Background alerts paused")).toBeTruthy();
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
  mount("/settings#notifications", transport);
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
    mount("/settings#notifications");
    expect(screen.getByText("Open from Home Screen")).toBeTruthy();
    expect(
      screen.getAllByText(/Add Harness to your Home Screen/).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Enable alerts" })).toBeNull();
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
  mount("/settings#notifications", transport);
  expect(enable).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Enable alerts" }));
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
  mount("/settings#notifications");
  expect(screen.getAllByText(/Add Harness to your Home Screen/)).toHaveLength(
    1,
  );
  const help = screen.getByText("Delivery help").closest("details")!;
  expect(help.open).toBe(false);
  fireEvent.click(screen.getByText("Delivery help"));
  expect(help.open).toBe(true);
  expect(help.textContent).toContain("Share → Add to Home Screen");
  expect(help.textContent).toContain("six hours");
  expect(screen.queryByLabelText("Notification permission")).toBeNull();
  expect(
    screen.queryByText(/Without background delivery, keep WebUI open/),
  ).toBeNull();
});

it.each([
  "completed",
  "failed",
  "suspended",
  "goal_verified",
  "goal_incomplete",
] as const)(
  "suppresses current-thread %s notices but retains other-thread notices regardless of opt-in",
  async (status) => {
    writePreference("notifications.enabled", "false");
    vi.mocked(document.hasFocus).mockReturnValue(true);
    event = notice("current", status);
    mount();
    fireEvent.click(screen.getByText("Emit"));
    expect(screen.queryByText(event.notice!.brief)).toBeNull();
    fireEvent.click(screen.getByText("Emit"));
    expect(screen.queryByText(event.notice!.brief)).toBeNull();
    event = { ...notice("unvisited", status), root_thread_id: "never-visited" };
    fireEvent.click(screen.getByText("Emit"));
    expect(screen.getAllByText(event.notice!.brief)).toHaveLength(1);
    fireEvent.click(screen.getAllByRole("button", { name: "Open chat" })[0]);
    expect(await screen.findByText("/threads/never-visited")).toBeTruthy();
    expect(native).not.toHaveBeenCalled();
  },
);

it("does not synthesize history and deduplicates receipts only within an epoch", () => {
  mount("/settings#notifications");
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
  mount("/threads/thread-other");
  expect(screen.getByText("Unavailable here")).toBeTruthy();
  expect(
    screen
      .getByRole("button", { name: "Send test notification" })
      .hasAttribute("disabled"),
  ).toBe(true);
  expect(screen.queryByRole("button", { name: "Enable alerts" })).toBeNull();
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
  const view = mount("/settings#notifications", transport);
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

it("uses the current route without replaying suppressed completion after navigation", () => {
  mount("/threads/thread-1?compose=1");
  fireEvent.click(screen.getByText("Emit"));
  expect(screen.queryByText(event.notice!.brief)).toBeNull();
  fireEvent.click(screen.getByRole("link", { name: "Other thread" }));
  fireEvent.click(screen.getByText("Emit"));
  expect(screen.queryByText(event.notice!.brief)).toBeNull();
  event = notice("new-receipt");
  fireEvent.click(screen.getByText("Emit"));
  expect(screen.getAllByText(event.notice!.brief)).toHaveLength(1);
  fireEvent.click(screen.getByRole("link", { name: "Current thread" }));
  event = notice("another-receipt");
  fireEvent.click(screen.getByText("Emit"));
  expect(screen.getAllByText(event.notice!.brief)).toHaveLength(1);
});

it.each([
  ["goal_verified", "Goal verified by the Agent"],
  ["goal_incomplete", "Goal not verified"],
] as const)(
  "presents %s from the receipt, not the currently cached Goal",
  async (status, title) => {
    queries.setQueryData(["thread", "thread-1", "detail"], {
      thread: { title: "UI polish", goal: { status: "working" } },
    });
    mount("/threads/thread-other");
    event = notice("goal-receipt", status);
    fireEvent.click(screen.getByText("Emit"));
    expect(await screen.findByText(`${title} - UI polish`)).toBeTruthy();
    expect(screen.queryByText("Task completed - UI polish")).toBeNull();
  },
);

it("retranslates local push failures without retrying and preserves browser details", async () => {
  permission = "granted";
  writePreference("notifications.push-subscription", "subscription-one");
  const transport = createTransport("fixture-key", vi.fn());
  vi.spyOn(push, "enablePush").mockResolvedValue();
  const failure = new push.PushError(
    "Browser push registration failed before a subscription could be saved to Harness UI.",
    {
      guidance:
        "Check this site's notification permission and browser push support.",
      detail: "NotAllowedError: browser detail",
      cause: new Error("browser detail"),
    },
  );
  const test = vi.spyOn(push, "testPush").mockRejectedValue(failure);
  mount("/settings#notifications", transport);
  try {
    await screen.findByText("Enabled on this device");
    fireEvent.click(
      screen.getByRole("button", { name: "Send test notification" }),
    );
    await screen.findByText("Subscription needs attention");
    expect(screen.getByRole("alert").textContent).toContain(
      "Browser detail: NotAllowedError: browser detail",
    );
    await act(async () => {
      await i18n.changeLanguage("zh-CN");
    });
    expect(screen.getByRole("alert").textContent).toContain(
      "浏览器推送注册失败",
    );
    expect(screen.getByRole("alert").textContent).toContain(
      "浏览器详情：NotAllowedError: browser detail",
    );
    expect(test).toHaveBeenCalledOnce();
  } finally {
    await act(async () => {
      await i18n.changeLanguage("en");
    });
    transport.close();
  }
});

it.each([
  { local: true, expected: "请先在浏览器中允许此网站发送通知。" },
  {
    local: false,
    expected: "Allow notifications in this site's browser settings first.",
  },
])(
  "distinguishes a $local local push failure from identical external text",
  async ({ local, expected }) => {
    permission = "granted";
    writePreference("notifications.push-subscription", "subscription-one");
    const transport = createTransport("fixture-key", vi.fn());
    vi.spyOn(push, "enablePush").mockResolvedValue();
    const message =
      "Allow notifications in this site's browser settings first.";
    const test = vi
      .spyOn(push, "testPush")
      .mockRejectedValue(
        local ? new push.PushError(message) : new Error(message),
      );
    mount("/settings#notifications", transport);
    try {
      await screen.findByText("Enabled on this device");
      fireEvent.click(
        screen.getByRole("button", { name: "Send test notification" }),
      );
      await screen.findByText("Subscription needs attention");
      await act(async () => {
        await i18n.changeLanguage("zh-CN");
      });
      expect(screen.getByRole("alert").textContent).toContain(expected);
      expect(test).toHaveBeenCalledOnce();
    } finally {
      await act(async () => {
        await i18n.changeLanguage("en");
      });
      transport.close();
    }
  },
);

function botMessage(id: string): Schema<"SummaryInvalidation"> {
  const event = notice("same-bot-receipt");
  return {
    ...event,
    root_thread_id: "thread_bot",
    thread_id: "thread_bot",
    notice: { ...event.notice!, message_id: id, brief: `Message ${id}` },
  };
}

it("presents distinct Bot messages from one run and opens the Bot conversation", async () => {
  mount("/settings/notifications");
  event = botMessage("one");
  fireEvent.click(screen.getByText("Emit"));
  fireEvent.click(screen.getByText("Emit"));
  expect(await screen.findAllByText("New message from Bot")).toHaveLength(1);
  event = botMessage("two");
  fireEvent.click(screen.getByText("Emit"));
  expect(await screen.findAllByText("New message from Bot")).toHaveLength(2);
  await act(() => i18n.changeLanguage("zh-CN"));
  expect(screen.getAllByText("Bot 给你发了消息")).toHaveLength(2);
  fireEvent.click(screen.getAllByRole("button", { name: "查看消息" })[0]);
  expect(screen.getByText("/bot")).toBeTruthy();
  await act(() => i18n.changeLanguage("en"));
});

it.each(["/bot", "/threads/thread_bot"])(
  "suppresses viewed Bot messages on %s without hiding other-thread completion or replaying after navigation",
  async (path) => {
    mount(path);
    const viewed = botMessage("viewed");
    event = viewed;
    fireEvent.click(screen.getByText("Emit"));
    expect(screen.queryByText("New message from Bot")).toBeNull();
    event = notice("other-thread-completion");
    fireEvent.click(screen.getByText("Emit"));
    expect(await screen.findByText("Task completed - UI polish")).toBeTruthy();
    fireEvent.click(screen.getByText("Other thread"));
    event = viewed;
    fireEvent.click(screen.getByText("Emit"));
    expect(screen.queryByText("New message from Bot")).toBeNull();
    event = botMessage("after-navigation");
    fireEvent.click(screen.getByText("Emit"));
    expect(await screen.findAllByText("New message from Bot")).toHaveLength(1);
  },
);

it.each(["completed", "failed", "suspended"] as const)(
  "suppresses a viewed Bot %s operation notice",
  (status) => {
    mount("/bot");
    event = {
      ...notice(`bot-${status}`, status),
      root_thread_id: "thread_bot",
      thread_id: "thread_bot",
    };
    fireEvent.click(screen.getByText("Emit"));
    expect(screen.queryByText(event.notice!.brief)).toBeNull();
  },
);

it("persists Bot-only scope, filters ordinary notices and keeps Bot attention", async () => {
  mount("/settings/notifications");
  fireEvent.click(screen.getByRole("button", { name: "Bot only" }));
  expect(localStorage.getItem("a13n-harness-ui.notifications.scope")).toBe(
    "bot",
  );
  event = notice("filtered-failure", "failed");
  fireEvent.click(screen.getByText("Emit"));
  expect(screen.queryByText("Task failed - UI polish")).toBeNull();
  event = {
    ...notice("bot-attention", "suspended"),
    root_thread_id: "thread_bot",
  };
  fireEvent.click(screen.getByText("Emit"));
  expect(await screen.findByText("Bot needs your response")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "All" }));
  event = notice("all-failure", "failed");
  fireEvent.click(screen.getByText("Emit"));
  expect(await screen.findByText("Task failed - UI polish")).toBeTruthy();
});

it("restores scope across reload and synchronizes existing push on scope and language changes", async () => {
  permission = "granted";
  writePreference("notifications.scope", "bot");
  writePreference("notifications.push-subscription", "subscription-one");
  const enable = vi.spyOn(push, "enablePush").mockResolvedValue();
  mount("/settings/notifications", createTransport("fixture-key", vi.fn()));
  expect(
    screen
      .getByRole("button", { name: "Bot only" })
      .getAttribute("aria-pressed"),
  ).toBe("true");
  await waitFor(() =>
    expect(enable).toHaveBeenCalledWith(expect.anything(), false),
  );
  enable.mockClear();
  fireEvent.click(screen.getByRole("button", { name: "All" }));
  await waitFor(() => expect(enable).toHaveBeenCalledOnce());
  enable.mockClear();
  await act(() => i18n.changeLanguage("zh-CN"));
  await waitFor(() => expect(enable).toHaveBeenCalledOnce());
  await act(() => i18n.changeLanguage("en"));
});

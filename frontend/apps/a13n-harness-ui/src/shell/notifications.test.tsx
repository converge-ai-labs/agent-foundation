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
  NotificationsProvider,
  useNotifications,
} from "./notifications";
import type { Schema } from "../transport/client";

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
function mount(path = "/threads/thread-1") {
  return render(
    <QueryClientProvider client={queries}>
      <MemoryRouter initialEntries={[path]}>
        <NotificationsProvider>
          <Controls />
        </NotificationsProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
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
      tag: "epoch-1:receipt-1",
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
  expect(screen.getByRole("alert").textContent).toContain("could not display");
});

it("allows foreground test notifications without claiming delivery and reports asynchronous failures", async () => {
  permission = "granted";
  vi.mocked(document.hasFocus).mockReturnValue(true);
  mount();
  fireEvent.click(
    screen.getByRole("button", { name: "Send test notification" }),
  );
  expect(native).toHaveBeenCalledOnce();
  expect(screen.getByText(/does not confirm/)).toBeTruthy();
  const notification = native.mock.results[0].value;
  act(() => notification.onshow());
  expect(screen.getByText(/browser reported/)).toBeTruthy();
  act(() => notification.onerror());
  expect(screen.getByRole("alert").textContent).toContain(
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

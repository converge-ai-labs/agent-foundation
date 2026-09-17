import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import { expect, it, vi } from "vitest";

function worker() {
  const handlers = new Map<string, (event: unknown) => void>();
  const self = {
    addEventListener: (type: string, callback: (event: unknown) => void) =>
      handlers.set(type, callback),
    skipWaiting: vi.fn(),
    location: { origin: "https://anui.example.test:8090" },
    clients: {
      claim: vi.fn(),
      matchAll: vi.fn(async () => [] as unknown[]),
      openWindow: vi.fn(),
    },
    registration: { showNotification: vi.fn() },
  };
  runInNewContext(
    readFileSync(new URL("../../public/sw.js", import.meta.url), "utf8"),
    { self, URL },
  );
  async function dispatch(type: string, event: object) {
    let pending: Promise<unknown> | undefined;
    handlers.get(type)!({
      ...event,
      waitUntil: (promise: Promise<unknown>) => {
        pending = promise;
      },
    });
    await pending;
  }
  return { self, handlers, dispatch };
}

it("handles only notification lifecycle, with no offline fetch interception", async () => {
  const { self, handlers, dispatch } = worker();
  expect([...handlers.keys()]).toEqual([
    "install",
    "activate",
    "push",
    "notificationclick",
  ]);
  await dispatch("install", {});
  await dispatch("activate", {});
  expect(self.skipWaiting).toHaveBeenCalledOnce();
  expect(self.clients.claim).toHaveBeenCalledOnce();
});

it.each([undefined, null, 1, "text", { path: "https://evil.test/" }])(
  "shows a safe visible fallback for %j",
  async (payload) => {
    const { self, dispatch } = worker();
    await dispatch("push", { data: { json: () => payload } });
    expect(self.registration.showNotification).toHaveBeenCalledWith(
      "Harness UI",
      expect.objectContaining({ data: { path: "/" } }),
    );
  },
);

it("shows a fallback for malformed JSON and a real preview for a valid push", async () => {
  const { self, dispatch } = worker();
  await dispatch("push", {
    data: {
      json: () => {
        throw new SyntaxError("invalid");
      },
    },
  });
  expect(self.registration.showNotification).toHaveBeenCalledOnce();
  await dispatch("push", {
    data: {
      json: () => ({
        title: "Task completed",
        body: "Fixed delivery.",
        tag: "receipt-one",
        path: "/threads/thread-one",
      }),
    },
  });
  expect(self.registration.showNotification).toHaveBeenLastCalledWith(
    "Task completed",
    expect.objectContaining({
      body: "Fixed delivery.",
      tag: "receipt-one",
      data: { path: "/threads/thread-one" },
    }),
  );
});

it("opens a same-origin conversation with no window and no login credentials", async () => {
  const { self, dispatch } = worker();
  const close = vi.fn();
  await dispatch("notificationclick", {
    notification: { close, data: { path: "/threads/thread-one" } },
  });
  expect(close).toHaveBeenCalledOnce();
  expect(self.clients.openWindow).toHaveBeenCalledWith(
    "https://anui.example.test:8090/threads/thread-one",
  );
});

it("focuses the matching conversation without reloading or replacing unsaved editors", async () => {
  const { self, dispatch } = worker();
  const focus = vi.fn();
  const navigate = vi.fn();
  const editor = { url: "https://anui.example.test:8090/settings", navigate };
  self.clients.matchAll.mockResolvedValue([
    editor,
    {
      url: "https://anui.example.test:8090/threads/thread-one",
      focus,
      navigate,
    },
  ]);
  await dispatch("notificationclick", {
    notification: { close: vi.fn(), data: { path: "/threads/thread-one" } },
  });
  expect(navigate).not.toHaveBeenCalled();
  expect(focus).toHaveBeenCalledOnce();
  expect(self.clients.openWindow).not.toHaveBeenCalled();
  self.clients.matchAll.mockResolvedValue([editor]);
  await dispatch("notificationclick", {
    notification: { close: vi.fn(), data: { path: "/threads/thread-one" } },
  });
  expect(navigate).not.toHaveBeenCalled();
  expect(self.clients.openWindow).toHaveBeenCalledWith(
    "https://anui.example.test:8090/threads/thread-one",
  );
});

it("never opens a cross-origin target from notification data", async () => {
  const { self, dispatch } = worker();
  await dispatch("notificationclick", {
    notification: { close: vi.fn(), data: { path: "//evil.test/" } },
  });
  expect(self.clients.openWindow).not.toHaveBeenCalled();
  expect(self.clients.matchAll).not.toHaveBeenCalled();
});

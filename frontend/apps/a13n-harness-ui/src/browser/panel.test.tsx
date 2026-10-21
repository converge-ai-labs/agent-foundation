// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TransportContext } from "../transport/context";
import type { Transport } from "../transport/client";
import { BrowserPanel } from "./panel";

const { connections } = vi.hoisted(() => ({
  connections: [] as {
    dispatchEvent: (event: Event) => boolean;
    viewOnly: boolean;
    disconnect: ReturnType<typeof vi.fn>;
    focus: ReturnType<typeof vi.fn>;
    sendKey: ReturnType<typeof vi.fn>;
  }[],
}));
vi.mock("@novnc/novnc", () => ({
  default: class extends EventTarget {
    viewOnly = false;
    scaleViewport = false;
    disconnect = vi.fn();
    focus = vi.fn();
    sendKey = vi.fn();
    constructor() {
      super();
      connections.push(this);
      setTimeout(() => this.dispatchEvent(new Event("connect")), 0);
    }
  },
}));
afterEach(() => {
  cleanup();
  connections.length = 0;
});
function setup(
  fullControl = true,
  installed = true,
  initialStatus = "ready",
  platform = "linux",
  resizing = true,
) {
  let control = "agent";
  let status = initialStatus;
  let sessionId = initialStatus === "disabled" ? "" : "browser-a";
  let runtimeError: string | null = null;
  let viewport = "desktop";
  const preparation = () => ({
    platform,
    supported: platform === "linux",
    ready: installed && platform === "linux",
    message:
      platform === "linux"
        ? "Install missing components on this Host."
        : "The remote desktop currently requires a Linux Host.",
    requirements: [
      {
        name: "Remote desktop",
        installed,
        detail: installed
          ? "Xtigervnc"
          : "Configured VNC executable is missing",
      },
      ...(!resizing
        ? [
            {
              name: "Desktop resizing",
              installed: false,
              required: false,
              detail: "Fixed-size desktop is available",
            },
          ]
        : []),
    ],
    steps:
      installed || platform !== "linux"
        ? []
        : [
            {
              title: "Install desktop components",
              command: "sudo apt-get install tigervnc-standalone-server",
            },
          ],
  });
  const viewports = [
    { preset: "desktop", label: "Desktop", width: 1280, height: 800 },
    { preset: "tablet", label: "Tablet", width: 768, height: 1024 },
    { preset: "phone", label: "Phone", width: 390, height: 844 },
  ];
  const state = () => ({
    session_id: sessionId,
    status,
    control,
    error: runtimeError,
    url: "https://example.com",
    viewport,
    viewports,
    resize_available: resizing,
  });
  const post = vi.fn(
    async (
      url: string,
      options: { body?: { control_token?: string; preset?: string } },
    ) => {
      if (url.endsWith("viewport")) {
        viewport = options.body!.preset!;
        return { data: state() };
      }
      if (url.endsWith("take-control")) {
        control = "human";
        return { data: { control_token: "controller-secret", state: state() } };
      }
      if (url.endsWith("handback")) {
        control = "agent";
        return { data: { control_token: "", state: state() } };
      }
      if (url.endsWith("enable")) {
        status = "enabled";
        sessionId = "browser-a";
      }
      if (url.endsWith("start") || url.endsWith("reset")) {
        status = "ready";
        control = "agent";
        sessionId = "browser-b";
        runtimeError = null;
      }
      if (url.endsWith("stop")) {
        status = "stopped";
        control = "agent";
        runtimeError = null;
      }
      if (url.endsWith("access"))
        return {
          data: {
            ticket: "single-use-ticket",
            password: "native-vnc-password",
            control: !!options.body?.control_token,
          },
        };
      return { data: {} };
    },
  );
  const client = {
    GET: vi.fn(async (url: string) => ({
      data: url.endsWith("/setup") ? preparation() : state(),
    })),
    POST: post,
  };
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = (threadId: string) => (
    <QueryClientProvider client={queries}>
      <TransportContext value={{ client } as unknown as Transport}>
        <BrowserPanel threadId={threadId} fullControl={fullControl} />
      </TransportContext>
    </QueryClientProvider>
  );
  const rendered = render(view("thread-a"));
  return {
    client,
    switchThread: (threadId: string) => {
      sessionId = "browser-c";
      rendered.rerender(view(threadId));
    },
    install: () => {
      installed = true;
    },
    stopExternally: async () => {
      status = "stopped";
      control = "agent";
      runtimeError = null;
      await queries.invalidateQueries({ queryKey: ["browser", "thread-a"] });
    },
  };
}
it("guides installation, copies a command, and requires a successful recheck before enablement", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  const original = Object.getOwnPropertyDescriptor(navigator, "clipboard");
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
  try {
    const { client, install } = setup(true, false, "disabled", "linux", false);
    await screen.findByText("Configured VNC executable is missing");
    expect(screen.getByText("Optional")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Enable browser" })).toBeNull();
    expect(client.POST).not.toHaveBeenCalled();
    fireEvent.click(
      screen.getByRole("button", {
        name: "Copy Install desktop components command",
      }),
    );
    await waitFor(() =>
      expect(writeText).toHaveBeenCalledWith(
        "sudo apt-get install tigervnc-standalone-server",
      ),
    );
    install();
    fireEvent.click(screen.getByRole("button", { name: "Check again" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Enable browser" }),
    );
    fireEvent.click(
      await screen.findByRole("button", { name: "Start browser" }),
    );
    await connected();
    fireEvent.click(screen.getByRole("button", { name: "Take control" }));
    await connected(true);
    const viewport = screen.getByRole("combobox", {
      name: "Browser viewport size",
    }) as HTMLSelectElement;
    expect(viewport.disabled).toBe(true);
    expect(viewport.title).toBe("Desktop resizing is unavailable on this Host");
    expect(client.POST).toHaveBeenCalledWith(
      expect.stringContaining("/enable"),
      expect.anything(),
    );
  } finally {
    if (original) Object.defineProperty(navigator, "clipboard", original);
    else Reflect.deleteProperty(navigator, "clipboard");
  }
});

it("keeps unsupported Hosts unavailable without offering Linux installation commands", async () => {
  const { client } = setup(true, true, "disabled", "macos");
  await screen.findByText("macOS Host");
  expect(
    screen.getByText("The remote desktop currently requires a Linux Host."),
  ).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Enable browser" })).toBeNull();
  expect(screen.queryByText("Install on the Host")).toBeNull();
  expect(client.POST).not.toHaveBeenCalled();
});

it("offers settings and manual rechecking without an installation action", async () => {
  const { client } = setup(true, false, "disabled");
  await screen.findByText("Configured VNC executable is missing");
  expect(
    screen
      .getByRole("link", { name: "Configure browser and VNC" })
      .getAttribute("href"),
  ).toBe("/settings#browser");
  expect(
    screen.queryByRole("button", { name: "Install browser support" }),
  ).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Check again" }));
  await waitFor(() =>
    expect(
      client.GET.mock.calls.filter(([url]) => url.endsWith("/setup")).length,
    ).toBeGreaterThan(1),
  );
  expect(client.POST).not.toHaveBeenCalled();
});

async function connected(control = false) {
  await waitFor(() => {
    expect(
      screen.getByLabelText("Remote browser desktop").getAttribute("aria-busy"),
    ).toBe("false");
    expect(!!screen.queryByRole("button", { name: "Read-only browser" })).toBe(
      !control,
    );
  });
}
it("starts read only, requests explicit takeover, and reconnects read only after handback", async () => {
  const { client } = setup();
  await connected();
  expect(connections.at(-1)?.viewOnly).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Take control" }));
  await connected(true);
  expect(connections.at(-1)?.viewOnly).toBe(false);
  expect(connections).toHaveLength(2);
  expect(client.POST).toHaveBeenCalledWith(
    expect.stringContaining("/access"),
    expect.objectContaining({
      body: { session_id: "browser-a", control_token: "controller-secret" },
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Hand back to Agent" }));
  await connected();
  expect(connections.at(-1)?.viewOnly).toBe(true);
  expect(connections).toHaveLength(3);
  cleanup();
  await waitFor(() =>
    expect(connections.at(-1)?.disconnect).toHaveBeenCalled(),
  );
  expect(client.POST.mock.calls.some(([url]) => url.endsWith("/stop"))).toBe(
    false,
  );
});
it("does not acquire Host browser access for a Sandbox conversation", () => {
  const { client } = setup(false);
  expect(
    screen.getByText(
      "Open a Full Control conversation to use the shared Host browser.",
    ),
  ).toBeTruthy();
  expect(client.GET).not.toHaveBeenCalled();
  expect(client.POST).not.toHaveBeenCalled();
});

it("explains read-only clicks without taking control and clears the hint on explicit takeover", async () => {
  const { client } = setup();
  await connected();
  const guard = screen.getByRole("button", { name: "Read-only browser" });
  fireEvent.pointerDown(guard);
  fireEvent.click(guard);
  await screen.findByText(
    "Read only. Select Take control to interact with this browser.",
  );
  expect(
    client.POST.mock.calls.some(([url]) => url.endsWith("/take-control")),
  ).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Take control" }));
  await connected(true);
  expect(
    screen.queryByRole("button", { name: "Read-only browser" }),
  ).toBeNull();
  expect(
    screen.queryByText(
      "Read only. Select Take control to interact with this browser.",
    ),
  ).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Hand back to Agent" }));
  await connected();
  expect(
    screen.getByRole("button", { name: "Read-only browser" }),
  ).toBeTruthy();
});

it("requires takeover to resize the viewport and preserves the live viewer connection", async () => {
  const { client } = setup();
  await connected();
  const selector = screen.getByRole("combobox", {
    name: "Browser viewport size",
  });
  expect(selector).toHaveProperty("disabled", true);
  fireEvent.click(screen.getByRole("button", { name: "Take control" }));
  await connected(true);
  expect(selector).toHaveProperty("disabled", false);
  const connection = connections.at(-1);
  fireEvent.change(selector, { target: { value: "phone" } });
  await waitFor(() => expect(selector).toHaveProperty("value", "phone"));
  await waitFor(() => expect(selector).toHaveProperty("disabled", false));
  expect(client.POST).toHaveBeenCalledWith(
    expect.stringContaining("/viewport"),
    expect.objectContaining({
      body: {
        session_id: "browser-a",
        control_token: "controller-secret",
        preset: "phone",
      },
    }),
  );
  expect(connections.at(-1)).toBe(connection);
  expect(connection?.disconnect).not.toHaveBeenCalled();
});

it("shows browser exit as stopped and starts a new read-only session without reusing input authority", async () => {
  const { client, stopExternally } = setup();
  await connected();
  fireEvent.click(screen.getByRole("button", { name: "Take control" }));
  await connected(true);
  const controller = connections.at(-1);
  await stopExternally();
  await screen.findByText("Browser stopped");
  expect(screen.queryByRole("alert")).toBeNull();
  expect(controller?.disconnect).toHaveBeenCalled();
  expect(
    screen.queryByRole("button", { name: "Hand back to Agent" }),
  ).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Start browser" }));
  await connected();
  expect(client.POST).toHaveBeenCalledWith(
    expect.stringContaining("/start"),
    expect.objectContaining({ body: { session_id: "browser-a" } }),
  );
  expect(client.POST).toHaveBeenLastCalledWith(
    expect.stringContaining("/access"),
    expect.objectContaining({
      body: { session_id: "browser-b", control_token: "" },
    }),
  );
});

it("stops without restarting until the user starts the browser", async () => {
  const { client } = setup();
  await connected();
  fireEvent.click(screen.getByRole("button", { name: "Stop browser" }));
  await screen.findByRole("button", { name: "Start browser" });
  expect(screen.queryByRole("button", { name: "Reset browser" })).toBeNull();
  expect(client.POST.mock.calls.some(([url]) => url.endsWith("/start"))).toBe(
    false,
  );
});

it("resets the browser and drops input authority for the replacement", async () => {
  const { client } = setup();
  await connected();
  fireEvent.click(screen.getByRole("button", { name: "Take control" }));
  await connected(true);
  const previous = connections.at(-1);
  fireEvent.click(screen.getByRole("button", { name: "Reset browser" }));
  await connected();
  expect(previous?.disconnect).toHaveBeenCalled();
  expect(client.POST).toHaveBeenCalledWith(
    expect.stringContaining("/reset"),
    expect.objectContaining({ body: { session_id: "browser-a" } }),
  );
  expect(client.POST).toHaveBeenLastCalledWith(
    expect.stringContaining("/access"),
    expect.objectContaining({
      body: { session_id: "browser-b", control_token: "" },
    }),
  );
});

it("reconnects a lost viewer without resetting or stopping the browser", async () => {
  const { client } = setup();
  await connected();
  act(() => {
    connections.at(-1)?.dispatchEvent(new Event("disconnect"));
  });
  fireEvent.click(
    await screen.findByRole("button", { name: "Reconnect viewer" }),
  );
  await connected();
  expect(client.POST).toHaveBeenLastCalledWith(
    expect.stringContaining("/access"),
    expect.objectContaining({
      body: { session_id: "browser-a", control_token: "" },
    }),
  );
  expect(
    client.POST.mock.calls.some(
      ([url]) => url.endsWith("/reset") || url.endsWith("/stop"),
    ),
  ).toBe(false);
});

it("never sends another conversation's controller token when switching windows", async () => {
  const { client, switchThread } = setup();
  await connected();
  fireEvent.click(screen.getByRole("button", { name: "Take control" }));
  await connected(true);
  act(() => switchThread("thread-b"));
  await connected();
  const otherAccess = client.POST.mock.calls.filter(
    ([url, options]) =>
      url.endsWith("/access") &&
      (options as unknown as { params: { path: { thread_id: string } } }).params
        .path.thread_id === "thread-b",
  );
  expect(otherAccess.length).toBeGreaterThan(0);
  for (const [, options] of otherAccess)
    expect(options.body?.control_token).toBe("");
  expect(connections.at(-1)?.viewOnly).toBe(true);
});

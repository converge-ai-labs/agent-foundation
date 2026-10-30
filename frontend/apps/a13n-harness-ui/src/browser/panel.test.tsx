// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TransportContext } from "../transport/context";
import type { Transport } from "../transport/client";
import { BrowserPanel } from "./panel";
vi.mock("./desktop", () => ({
  Desktop: ({
    threadId,
    controlToken,
    scale,
    actions,
    busy,
    onReconnectControl,
    onControlActivity,
  }: {
    threadId: string;
    controlToken: string;
    scale: string;
    actions?: React.ReactNode;
    busy: boolean;
    onReconnectControl: () => void;
    onControlActivity: () => void;
  }) => (
    <>
      {actions}
      <button onClick={onReconnectControl}>Reconnect control</button>
      <button onClick={onControlActivity}>Control activity</button>
      <div data-testid="stream" data-scale={scale} data-busy={busy}>
        {threadId}:{controlToken || "readonly"}
      </div>
    </>
  ),
}));
afterEach(cleanup);
function show({
  platform = "linux",
  resizeAvailable = true,
  status = "ready",
  control = "agent",
  pageCount = 1,
  threadId = "thread-a",
  fullControl = true,
  setup = {
    ready: true,
    supported: true,
    platform: "linux",
    requirements: [] as {
      name: string;
      installed: boolean;
      detail: string;
      required: boolean;
    }[],
    message: "",
  },
} = {}) {
  let state = {
    platform,
    session_id: status === "disabled" ? "" : "binding-a",
    status,
    page_count: status === "ready" ? pageCount : 0,
    control,
    control_episode: 7,
    control_remaining_seconds: 300,
    url: "https://example.com",
    viewport: "desktop",
    resize_available: resizeAvailable,
    viewports: [
      { preset: "desktop", label: "Desktop", width: 1280, height: 800 },
      { preset: "phone", label: "Phone", width: 390, height: 844 },
    ],
  };
  const client = {
    GET: vi.fn(async (url: string) => ({
      data: url.endsWith("/setup") ? setup : state,
    })),
    POST: vi.fn(async (url: string, options: { body?: { preset: string } }) => {
      if (url.endsWith("take-control")) {
        state = { ...state, control: "human" };
        return { data: { state, control_token: "controller-a" } };
      }
      if (url.endsWith("handback")) {
        state = { ...state, control: "agent" };
        return { data: { state, control_token: "" } };
      }
      if (url.endsWith("close"))
        state = {
          ...state,
          page_count: 0,
          control: "agent",
        };
      if (url.endsWith("reset"))
        state = {
          ...state,
          status: "ready",
          page_count: 1,
          control: "agent",
          session_id: "binding-reset",
        };
      if (url.endsWith("start"))
        state = {
          ...state,
          status: "ready",
          page_count: 1,
          control: "agent",
          session_id: "binding-started",
        };
      if (url.endsWith("viewport"))
        state = {
          ...state,
          viewport: options.body!.preset,
        };
      return { data: state };
    }),
  };
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = (threadId: string) => (
    <QueryClientProvider client={queries}>
      <TransportContext value={{ client } as unknown as Transport}>
        <BrowserPanel
          threadId={threadId || undefined}
          fullControl={fullControl}
        />
      </TransportContext>
    </QueryClientProvider>
  );
  const result = render(view(threadId));
  return {
    client,
    queries,
    switchThread: () => result.rerender(view("thread-b")),
  };
}
async function openBrowser() {
  fireEvent.click(await screen.findByRole("button", { name: "Open browser" }));
}
it("opens and closes conversation tabs without an enable switch", async () => {
  const { client } = show({ status: "enabled" });
  await openBrowser();
  await screen.findByTestId("stream");
  expect(screen.queryByRole("switch")).toBeNull();
  await userEvent.click(
    screen.getByRole("button", { name: "Browser options" }),
  );
  await userEvent.click(
    await screen.findByRole("menuitem", { name: "Close conversation tabs" }),
  );
  await waitFor(() => expect(screen.queryByTestId("stream")).toBeNull());
  await openBrowser();
  await screen.findByTestId("stream");
  expect(client.POST.mock.calls.map(([url]) => url)).toEqual([
    "/api/threads/{thread_id}/browser/start",
    "/api/threads/{thread_id}/browser/close",
    "/api/threads/{thread_id}/browser/start",
  ]);
  expect(client.POST).toHaveBeenLastCalledWith(
    "/api/threads/{thread_id}/browser/start",
    { params: { path: { thread_id: "thread-a" } } },
  );
});
it("uses one page stream for read-only and explicit control, then clears authority on handback", async () => {
  show();
  expect((await screen.findByTestId("stream")).textContent).toBe(
    "thread-a:readonly",
  );
  fireEvent.click(screen.getByRole("button", { name: "Take control" }));
  await screen.findByRole("button", { name: "Hand back to Agent" });
  expect(screen.getByTestId("stream").textContent).toBe(
    "thread-a:controller-a",
  );
  fireEvent.click(screen.getByRole("button", { name: "Hand back to Agent" }));
  await screen.findByRole("button", { name: "Take control" });
  expect(screen.getByTestId("stream").textContent).toBe("thread-a:readonly");
});
it("transfers control here with one explicit command and keeps handback available", async () => {
  const { client } = show({ control: "human" });
  const transfer = await screen.findByRole("button", {
    name: "Take control here",
  });
  expect(screen.getByTestId("stream").textContent).toBe("thread-a:readonly");
  expect(
    screen.getByRole("button", { name: "Hand back to Agent" }),
  ).toBeTruthy();
  expect(client.POST).not.toHaveBeenCalled();
  fireEvent.click(transfer);
  await waitFor(() =>
    expect(screen.getByTestId("stream").textContent).toBe(
      "thread-a:controller-a",
    ),
  );
  expect(client.POST.mock.calls.map(([url]) => url)).toEqual([
    "/api/threads/{thread_id}/browser/take-control",
  ]);
  expect(
    screen.queryByRole("button", { name: "Take control here" }),
  ).toBeNull();
});
it("can hand back from a fresh viewer without acquiring control", async () => {
  const { client } = show({ control: "human" });
  fireEvent.click(
    await screen.findByRole("button", { name: "Hand back to Agent" }),
  );
  await screen.findByRole("button", { name: "Take control" });
  expect(client.POST.mock.calls.map(([url]) => url)).toEqual([
    "/api/threads/{thread_id}/browser/handback",
  ]);
});
it("never passes the previous conversation's control token to another stream", async () => {
  const view = show();
  await screen.findByTestId("stream");
  fireEvent.click(screen.getByRole("button", { name: "Take control" }));
  await screen.findByRole("button", { name: "Hand back to Agent" });
  view.switchThread();
  await screen.findByText("thread-b:readonly");
});

it.each(["linux", "macos"])(
  "hands back globally after switching Threads on %s",
  async (platform) => {
    const view = show({ platform });
    fireEvent.click(
      await screen.findByRole("button", {
        name: platform === "macos" ? "Pause automation" : "Take control",
      }),
    );
    await screen.findByRole("button", {
      name: platform === "macos" ? "Resume automation" : "Hand back to Agent",
    });
    view.switchThread();
    await screen.findByText("thread-b:readonly");
    fireEvent.click(
      screen.getByRole("button", {
        name: platform === "macos" ? "Resume automation" : "Hand back to Agent",
      }),
    );
    await screen.findByRole("button", {
      name: platform === "macos" ? "Pause automation" : "Take control",
    });
    expect(view.client.POST).toHaveBeenLastCalledWith(
      "/api/threads/{thread_id}/browser/handback",
      {
        params: { path: { thread_id: "thread-b" } },
        body: { session_id: "binding-a", control_episode: 7 },
      },
    );
  },
);

it("transfers global control after switching Threads without a handback", async () => {
  const view = show();
  fireEvent.click(await screen.findByRole("button", { name: "Take control" }));
  await screen.findByText("thread-a:controller-a");
  view.switchThread();
  await screen.findByText("thread-b:readonly");
  fireEvent.click(screen.getByRole("button", { name: "Take control here" }));
  await screen.findByText("thread-b:controller-a");
  expect(view.client.POST).toHaveBeenLastCalledWith(
    "/api/threads/{thread_id}/browser/take-control",
    {
      params: { path: { thread_id: "thread-b" } },
      body: { session_id: "binding-a" },
    },
  );
  expect(view.client.POST).toHaveBeenCalledTimes(2);
});

it.each(["linux", "macos"])(
  "keeps global handback available without local pages on %s",
  async (platform) => {
    const { client } = show({ platform, pageCount: 0, control: "human" });
    fireEvent.click(
      await screen.findByRole("button", {
        name: platform === "macos" ? "Resume automation" : "Hand back to Agent",
      }),
    );
    await screen.findByRole("button", { name: "Open browser" });
    expect(client.POST.mock.calls.map(([url]) => url)).toEqual([
      "/api/threads/{thread_id}/browser/handback",
    ]);
  },
);

it("keeps local zoom available without control and exposes scoped lifecycle buttons", async () => {
  const { client } = show();
  const user = userEvent.setup();
  await screen.findByTestId("stream");
  expect(screen.getByRole("button", { name: "Reset browser" })).toBeTruthy();
  expect(screen.queryByRole("switch")).toBeNull();
  expect(screen.queryByLabelText("Page address")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Browser options" }));
  expect(
    (await screen.findByRole("menuitem", { name: /Viewport/ })).getAttribute(
      "aria-disabled",
    ),
  ).toBe("true");
  expect(screen.queryByRole("menuitem", { name: /Aspect ratio/ })).toBeNull();
  await user.click(await screen.findByRole("menuitem", { name: /Zoom/ }));
  fireEvent.click(await screen.findByRole("menuitemradio", { name: "150%" }));
  expect(screen.getByTestId("stream").getAttribute("data-scale")).toBe("150");
  expect(client.POST).not.toHaveBeenCalled();
});

it("binds viewport changes to current control", async () => {
  const { client } = show();
  const user = userEvent.setup();
  await screen.findByTestId("stream");
  await user.click(screen.getByRole("button", { name: "Take control" }));
  await screen.findByRole("button", { name: "Hand back to Agent" });
  await user.click(screen.getByRole("button", { name: "Browser options" }));
  await user.click(await screen.findByRole("menuitem", { name: /Viewport/ }));
  fireEvent.click(
    await screen.findByRole("menuitemradio", { name: "Phone (390 × 844)" }),
  );
  await waitFor(() =>
    expect(client.POST).toHaveBeenCalledWith(
      expect.stringContaining("/viewport"),
      expect.objectContaining({
        body: {
          session_id: "binding-a",
          control_token: "controller-a",
          preset: "phone",
        },
      }),
    ),
  );
});

it("keeps zoom usable after takeover when the Host cannot resize", async () => {
  const { client } = show({ resizeAvailable: false });
  const user = userEvent.setup();
  await screen.findByTestId("stream");
  await user.click(screen.getByRole("button", { name: "Take control" }));
  await screen.findByRole("button", { name: "Hand back to Agent" });
  await user.click(screen.getByRole("button", { name: "Browser options" }));
  const viewport = await screen.findByRole("menuitem", { name: /Viewport/ });
  expect(viewport.getAttribute("aria-disabled")).toBe("true");
  expect(viewport.title).toBe("Viewport resizing is unavailable on this Host");
  await user.click(await screen.findByRole("menuitem", { name: /Zoom/ }));
  fireEvent.click(await screen.findByRole("menuitemradio", { name: "75%" }));
  expect(screen.getByTestId("stream").getAttribute("data-scale")).toBe("75");
  expect(
    client.POST.mock.calls.some(([url]) => url.endsWith("/viewport")),
  ).toBe(false);
});

it("resets and stops only the current conversation and clears its control authority", async () => {
  const { client } = show();
  await screen.findByTestId("stream");
  fireEvent.click(screen.getByRole("button", { name: "Take control" }));
  await screen.findByRole("button", { name: "Hand back to Agent" });
  fireEvent.click(screen.getByRole("button", { name: "Reset browser" }));
  await screen.findByRole("button", { name: "Take control" });
  expect(screen.getByTestId("stream").textContent).toBe("thread-a:readonly");
  expect(client.POST).toHaveBeenCalledWith(
    "/api/threads/{thread_id}/browser/reset",
    expect.objectContaining({
      params: { path: { thread_id: "thread-a" } },
      body: { session_id: "binding-a" },
    }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Browser options" }),
  );
  await userEvent.click(
    await screen.findByRole("menuitem", { name: "Close conversation tabs" }),
  );
  await waitFor(() => expect(screen.queryByTestId("stream")).toBeNull());
});

it.each([true, false])(
  "asks to start a conversation before checking permissions (%s)",
  (fullControl) => {
    const { client } = show({ threadId: "", fullControl });
    expect(screen.getByText("Start a conversation first")).toBeTruthy();
    expect(screen.queryByText("Browser unavailable")).toBeNull();
    expect(client.GET).not.toHaveBeenCalled();
    expect(client.POST).not.toHaveBeenCalled();
  },
);
it("keeps the permission explanation for an existing non-Full-Control conversation", () => {
  const { client } = show({ fullControl: false });
  expect(screen.getByText("Use a Full Control conversation.")).toBeTruthy();
  expect(client.GET).not.toHaveBeenCalled();
});
it("only checks components after a failed attempt and retries the full startup", async () => {
  const { client } = show({
    status: "enabled",
    setup: {
      ready: false,
      supported: true,
      platform: "linux",
      message: "Resolve missing components.",
      requirements: [
        {
          name: "Browser",
          installed: true,
          required: true,
          detail: "/usr/bin/chromium",
        },
        {
          name: "VNC server",
          installed: false,
          required: true,
          detail: "Install TigerVNC.",
        },
        {
          name: "Window management",
          installed: false,
          required: false,
          detail: "Optional window manager",
        },
      ],
    },
  });
  await waitFor(() => expect(client.GET).toHaveBeenCalledTimes(1));
  expect(screen.queryByLabelText("Browser troubleshooting")).toBeNull();
  client.POST.mockRejectedValueOnce(new Error("Missing VNC"));
  await openBrowser();
  await screen.findByText("Install TigerVNC.");
  expect(screen.queryByText("/usr/bin/chromium")).toBeNull();
  expect(screen.queryByText("Optional window manager")).toBeNull();
  expect(screen.queryByRole("switch")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  await screen.findByTestId("stream");
  expect(screen.queryByLabelText("Browser troubleshooting")).toBeNull();
});
it("shows unsupported platforms without dependency installation or settings detours", async () => {
  const { client } = show({
    status: "enabled",
    setup: {
      ready: false,
      supported: false,
      platform: "windows",
      requirements: [],
      message: "The Host browser requires Linux or macOS.",
    },
  });
  client.POST.mockRejectedValueOnce(new Error("Unsupported platform"));
  await openBrowser();
  await screen.findByText("Linux or macOS Host required");
  expect(
    screen.queryByRole("list", { name: "Browser requirements" }),
  ).toBeNull();
  expect(
    screen.queryByRole("link", { name: "Advanced browser settings" }),
  ).toBeNull();
  expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
});
it("keeps runtime failures visible when component checks succeed", async () => {
  const { client } = show({ status: "enabled" });
  client.POST.mockRejectedValueOnce(new Error("Profile is in use"));
  await openBrowser();
  await screen.findByText("Profile is in use");
  expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
});

it("pauses Mac automation for local operation and resumes it without remote takeover", async () => {
  const { client } = show({ platform: "macos", resizeAvailable: false });
  await screen.findByTestId("stream");
  expect(screen.queryByRole("button", { name: "Take control" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Pause automation" }));
  await screen.findByRole("button", { name: "Resume automation" });
  expect(client.POST).toHaveBeenCalledWith(
    "/api/threads/{thread_id}/browser/take-control",
    expect.objectContaining({ body: { session_id: "binding-a" } }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Resume automation" }));
  await screen.findByRole("button", { name: "Pause automation" });
  expect(client.POST).toHaveBeenLastCalledWith(
    "/api/threads/{thread_id}/browser/handback",
    expect.objectContaining({
      body: { session_id: "binding-a", control_episode: 7 },
    }),
  );
});

it("unblocks input from the takeover response without waiting for another state request", async () => {
  const { client } = show();
  await screen.findByTestId("stream");
  client.GET.mockImplementation(() => new Promise(() => {}));
  fireEvent.click(screen.getByRole("button", { name: "Take control" }));
  await screen.findByRole("button", { name: "Hand back to Agent" });
  await waitFor(() =>
    expect(screen.getByTestId("stream").getAttribute("data-busy")).toBe(
      "false",
    ),
  );
  expect(screen.getByTestId("stream").textContent).toBe(
    "thread-a:controller-a",
  );
});

it("ignores a stale poll response and keeps the newly acquired authority", async () => {
  const { client, queries } = show();
  await screen.findByTestId("stream");
  const old = queries.getQueryData(["browser", "thread-a"]);
  let finish!: (value: { data: typeof old }) => void;
  client.GET.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve as typeof finish;
      }),
  );
  void queries.invalidateQueries({ queryKey: ["browser", "thread-a"] });
  await waitFor(() => expect(finish).toBeDefined());
  fireEvent.click(screen.getByRole("button", { name: "Take control" }));
  await screen.findByRole("button", { name: "Hand back to Agent" });
  await act(async () => finish({ data: old }));
  expect(screen.getByTestId("stream").textContent).toBe(
    "thread-a:controller-a",
  );
});

it("renews control directly on explicit reconnect without a handback gap", async () => {
  const { client } = show();
  await screen.findByTestId("stream");
  fireEvent.click(screen.getByRole("button", { name: "Take control" }));
  await screen.findByRole("button", { name: "Hand back to Agent" });
  fireEvent.click(screen.getByRole("button", { name: "Reconnect control" }));
  await waitFor(() => expect(client.POST).toHaveBeenCalledTimes(2));
  expect(client.POST.mock.calls.map(([url]) => url)).toEqual([
    "/api/threads/{thread_id}/browser/take-control",
    "/api/threads/{thread_id}/browser/take-control",
  ]);
});

it("renews on controller activity and sends the observed episode on handback", async () => {
  const { client } = show();
  await userEvent.click(
    await screen.findByRole("button", { name: "Take control" }),
  );
  await waitFor(() =>
    expect(screen.getByTestId("stream").textContent).toContain("controller-a"),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Control activity" }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Control activity" }),
  );
  await waitFor(() =>
    expect(
      client.POST.mock.calls.filter(([url]) => url.endsWith("renew-control")),
    ).toHaveLength(1),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Hand back to Agent" }),
  );
  await waitFor(() =>
    expect(client.POST).toHaveBeenCalledWith(
      expect.stringMatching(/handback$/),
      expect.objectContaining({
        body: { session_id: "binding-a", control_episode: 7 },
      }),
    ),
  );
});

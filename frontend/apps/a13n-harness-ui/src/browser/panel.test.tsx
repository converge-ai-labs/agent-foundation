// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
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
  }: {
    threadId: string;
    controlToken: string;
    scale: string;
    actions?: React.ReactNode;
  }) => (
    <>
      {actions}
      <div data-testid="stream" data-scale={scale}>
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
    control: "agent",
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
      if (url.endsWith("enable"))
        state = { ...state, status: "enabled", session_id: "binding-enabled" };
      if (url.endsWith("take-control")) {
        state = { ...state, control: "human" };
        return { data: { state, control_token: "controller-a" } };
      }
      if (url.endsWith("handback")) {
        state = { ...state, control: "agent" };
        return { data: { state, control_token: "" } };
      }
      if (url.endsWith("stop"))
        state = {
          ...state,
          status: "stopped",
          control: "agent",
          session_id: "binding-stopped",
        };
      if (url.endsWith("reset"))
        state = {
          ...state,
          status: "ready",
          control: "agent",
          session_id: "binding-reset",
        };
      if (url.endsWith("start"))
        state = {
          ...state,
          status: "ready",
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
  return { client, switchThread: () => result.rerender(view("thread-b")) };
}
async function toggleBrowser() {
  const toggle = screen.getByRole("switch", { name: "Enable browser" });
  await waitFor(() =>
    expect(toggle.getAttribute("aria-disabled")).not.toBe("true"),
  );
  fireEvent.click(toggle);
}
it("starts a new browser with one toggle and restarts without enabling again", async () => {
  const { client } = show({ status: "disabled" });
  await toggleBrowser();
  await screen.findByTestId("stream");
  expect(client.POST.mock.calls.map(([url]) => url)).toEqual([
    "/api/threads/{thread_id}/browser/enable",
    "/api/threads/{thread_id}/browser/start",
  ]);
  expect(client.POST).toHaveBeenLastCalledWith(
    "/api/threads/{thread_id}/browser/start",
    expect.objectContaining({ body: { session_id: "binding-enabled" } }),
  );
  await toggleBrowser();
  await waitFor(() => expect(screen.queryByTestId("stream")).toBeNull());
  await toggleBrowser();
  await screen.findByTestId("stream");
  expect(client.POST.mock.calls.map(([url]) => url)).toEqual([
    "/api/threads/{thread_id}/browser/enable",
    "/api/threads/{thread_id}/browser/start",
    "/api/threads/{thread_id}/browser/stop",
    "/api/threads/{thread_id}/browser/start",
  ]);
  expect(client.POST).toHaveBeenLastCalledWith(
    "/api/threads/{thread_id}/browser/start",
    expect.objectContaining({ body: { session_id: "binding-stopped" } }),
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
it("never passes the previous conversation's control token to another stream", async () => {
  const view = show();
  await screen.findByTestId("stream");
  fireEvent.click(screen.getByRole("button", { name: "Take control" }));
  await screen.findByRole("button", { name: "Hand back to Agent" });
  view.switchThread();
  await screen.findByText("thread-b:readonly");
});

it("keeps local zoom available without control and exposes scoped lifecycle buttons", async () => {
  const { client } = show();
  const user = userEvent.setup();
  await screen.findByTestId("stream");
  expect(screen.getByRole("button", { name: "Reset browser" })).toBeTruthy();
  expect(
    screen
      .getByRole("switch", { name: "Enable browser" })
      .getAttribute("aria-checked"),
  ).toBe("true");
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
  expect(client.POST).toHaveBeenCalledWith(
    expect.stringContaining("/viewport"),
    expect.objectContaining({
      body: {
        session_id: "binding-a",
        control_token: "controller-a",
        preset: "phone",
      },
    }),
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
  await toggleBrowser();
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
    status: "disabled",
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
  await toggleBrowser();
  await screen.findByText("Install TigerVNC.");
  expect(screen.queryByText("/usr/bin/chromium")).toBeNull();
  expect(screen.queryByText("Optional window manager")).toBeNull();
  expect(screen.getByRole("switch").getAttribute("aria-checked")).toBe("false");
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  await screen.findByTestId("stream");
  expect(screen.queryByLabelText("Browser troubleshooting")).toBeNull();
});
it("shows unsupported platforms without dependency installation or settings detours", async () => {
  const { client } = show({
    status: "disabled",
    setup: {
      ready: false,
      supported: false,
      platform: "windows",
      requirements: [],
      message: "The Host browser requires Linux or macOS.",
    },
  });
  client.POST.mockRejectedValueOnce(new Error("Unsupported platform"));
  await toggleBrowser();
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
  const { client } = show({ status: "disabled" });
  client.POST.mockRejectedValueOnce(new Error("Profile is in use"));
  await toggleBrowser();
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
    expect.objectContaining({ body: { session_id: "binding-a" } }),
  );
});

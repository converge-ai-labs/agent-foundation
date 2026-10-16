import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { DesktopAssistance } from "./desktop-assistance";
import { ApiError } from "../../../service-client";

const { get, post, viewers, resolved, refresh } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  viewers: [] as Array<
    EventTarget & { viewOnly: boolean; disconnect: ReturnType<typeof vi.fn> }
  >,
  resolved: vi.fn(),
  refresh: vi.fn(),
}));
vi.mock("../../../auth/context", () => ({
  useClient: () => ({ workspace: () => ({ GET: get, POST: post }) }),
}));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws" } }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (value: string) => value }),
}));
vi.mock("@novnc/novnc/lib/rfb", () => ({
  default: {
    default: class extends EventTarget {
      viewOnly = false;
      scaleViewport = false;
      disconnect = vi.fn();
      constructor() {
        super();
        viewers.push(this);
      }
    },
  },
}));
const view = {
  id: "assistance",
  run_id: "run",
  tool_call_id: "help",
  environment_id: "env",
  mount: "workspace",
  reason: "Please choose a file",
  state: "awaiting_user",
  epoch: 0,
  controller_id: null,
  deadline: "2099-01-01",
  lease_expires_at: null,
  failure: null,
  outcome: null,
};
const reply = (value: object | null) => ({
  data: value,
  response: new Response(),
});
function start(userControl = false, state: string | null = "awaiting_user") {
  get.mockResolvedValue(reply(state === null ? null : { ...view, state }));
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const component = render(
    <QueryClientProvider client={cache}>
      <DesktopAssistance
        runId="run"
        callId={userControl ? undefined : "help"}
        canContinue={userControl}
        onResolved={resolved}
        onRefresh={refresh}
      />
    </QueryClientProvider>,
  );
  return { cache, ...component };
}
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  viewers.length = 0;
});

it.each(["open", "request"])(
  "explains an Agent-won race during %s without claiming or resuming",
  async (action) => {
    const user = userEvent.setup();
    start(true, action === "open" ? null : "viewing");
    post.mockRejectedValueOnce(
      new ApiError(
        409,
        "conflict",
        "internal identifier",
        { reason: "desktop_agent_assistance_pending" },
        null,
      ),
    );
    await user.click(
      await screen.findByRole("button", {
        name: action === "open" ? "Open desktop" : "Get control",
      }),
    );
    await screen.findByText(
      "The agent has requested desktop assistance. Switching to its assistance card…",
    );
    expect(screen.queryByText("internal identifier")).toBeNull();
    expect(screen.queryByRole("button", { name: "Get control" })).toBeNull();
    expect(refresh).toHaveBeenCalled();
    expect(post).toHaveBeenCalledTimes(1);
    expect(viewers).toHaveLength(0);
  },
);

it("cannot acquire control from a late request response after switching panels", async () => {
  const user = userEvent.setup();
  const { unmount } = start(true, "viewing");
  let respond!: (value: object) => void;
  post.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        respond = resolve;
      }),
  );
  await user.click(await screen.findByRole("button", { name: "Get control" }));
  unmount();
  get.mockResolvedValue(reply(view));
  respond(reply(view));
  await Promise.resolve();
  expect(post).toHaveBeenCalledTimes(1);
  expect(viewers).toHaveLength(0);
});

it("revokes its read-only viewer before requesting takeover so a competing card inherits no connection", async () => {
  const user = userEvent.setup();
  start(true, "viewing");
  post.mockResolvedValueOnce(
    reply({
      assistance: { ...view, state: "viewing" },
      mode: "view",
      websocket_url: "wss://desktop.example/",
      password: "private",
      expires_in_seconds: 60,
    }),
  );
  await user.click(
    await screen.findByRole("button", { name: "Open read-only desktop" }),
  );
  await waitFor(() => expect(viewers).toHaveLength(1));
  post.mockResolvedValueOnce(reply({ ...view, state: "viewing" }));
  post.mockResolvedValueOnce(reply({ ...view, state: "preparing" }));
  get.mockResolvedValue(reply({ ...view, state: "preparing" }));
  await user.click(screen.getByRole("button", { name: "Get control" }));
  await screen.findByText(
    "Pausing agent. Waiting for admitted operations to finish…",
  );
  expect(post.mock.calls.map(([path]) => path.split("/").at(-1))).toEqual([
    "view",
    "disconnect",
    "request-control",
  ]);
  expect(viewers[0].disconnect).toHaveBeenCalled();
  expect(viewers[0].viewOnly).toBe(true);
  expect(resolved).not.toHaveBeenCalled();
});

it("keeps input disabled until a confirmed control grant connects; never caches credentials", async () => {
  const user = userEvent.setup();
  const { cache, unmount } = start();
  post.mockResolvedValue(
    reply({
      assistance: { ...view, state: "human_control", epoch: 1 },
      mode: "control",
      websocket_url: "wss://desktop.example/?token=secret",
      password: "private",
      expires_in_seconds: 60,
    }),
  );
  await user.click(await screen.findByRole("button", { name: "Take control" }));
  await waitFor(() => expect(viewers).toHaveLength(1));
  expect(viewers[0].viewOnly).toBe(true);
  viewers[0].dispatchEvent(new Event("connect"));
  await screen.findByText("You control this desktop. The agent is paused.");
  expect(viewers[0].viewOnly).toBe(false);
  expect(
    JSON.stringify(
      cache
        .getQueryCache()
        .getAll()
        .map((item) => item.state.data),
    ),
  ).not.toContain("private");
  expect(resolved).not.toHaveBeenCalled();
  unmount();
  expect(viewers[0].viewOnly).toBe(true);
  expect(viewers[0].disconnect).toHaveBeenCalled();
  expect(post).toHaveBeenLastCalledWith(
    "/api/v1/desktop-assistance/{assistance_id}/disconnect",
    expect.objectContaining({ body: expect.objectContaining({ epoch: 1 }) }),
  );
});

it("read-only grants never enable input and completion waits for confirmed server cleanup", async () => {
  const user = userEvent.setup();
  start();
  post.mockResolvedValueOnce(
    reply({
      assistance: view,
      mode: "view",
      websocket_url: "wss://desktop.example/",
      password: "private",
      expires_in_seconds: 60,
    }),
  );
  await user.click(
    await screen.findByRole("button", { name: "Open read-only desktop" }),
  );
  await waitFor(() => expect(viewers).toHaveLength(1));
  viewers[0].dispatchEvent(new Event("connect"));
  await screen.findByText("Read-only desktop");
  expect(viewers[0].viewOnly).toBe(true);
  let complete!: (value: object) => void;
  post.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        complete = resolve;
      }),
  );
  await user.click(screen.getByRole("button", { name: "Finish assistance" }));
  expect(viewers[0].disconnect).toHaveBeenCalled();
  expect(resolved).not.toHaveBeenCalled();
  const outcome = { assistance_id: "assistance", outcome: "completed" };
  complete(reply({ ...view, state: "ready_to_resume", outcome }));
  await waitFor(() => expect(resolved).toHaveBeenCalledWith(outcome));
});

it("does not resolve the deferred call when revocation fails", async () => {
  const user = userEvent.setup();
  start();
  post.mockRejectedValueOnce(new Error("Cleanup unconfirmed"));
  await user.click(
    await screen.findByRole("button", { name: "Finish assistance" }),
  );
  await screen.findByText("Cleanup unconfirmed");
  expect(resolved).not.toHaveBeenCalled();
});

it("waits for the durable pause before requesting a user control grant", async () => {
  const user = userEvent.setup();
  const { cache } = start(true, "viewing");
  post.mockResolvedValueOnce(reply({ ...view, state: "preparing" }));
  get.mockResolvedValue(reply({ ...view, state: "preparing" }));
  await user.click(await screen.findByRole("button", { name: "Get control" }));
  await screen.findByText(
    "Pausing agent. Waiting for admitted operations to finish…",
  );
  expect(post).toHaveBeenCalledTimes(1);
  expect(post.mock.calls[0][0]).toContain("/request-control");
  expect(viewers).toHaveLength(0);
  get.mockResolvedValue(reply(view));
  post.mockResolvedValueOnce(
    reply({
      assistance: { ...view, state: "human_control", epoch: 1 },
      mode: "control",
      websocket_url: "wss://desktop.example/",
      password: "private",
      expires_in_seconds: 60,
    }),
  );
  await cache.invalidateQueries();
  await waitFor(() => expect(viewers).toHaveLength(1));
  expect(post.mock.calls[1][0]).toMatch(/\/control$/);
  expect(viewers[0].viewOnly).toBe(true);
});

it("resumes only after confirmed handback, and never after failed cleanup", async () => {
  const user = userEvent.setup();
  start(true);
  let finish!: (value: object) => void;
  post.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  await user.click(
    await screen.findByRole("button", { name: "Return control and continue" }),
  );
  expect(post).toHaveBeenCalledTimes(1);
  expect(post.mock.calls[0][0]).toMatch(/\/complete$/);
  finish(
    reply({
      ...view,
      state: "ready_to_resume",
      outcome: { outcome: "completed" },
    }),
  );
  post.mockResolvedValueOnce(reply({ id: "next" }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
  expect(post.mock.calls[1][0]).toMatch(/\/resume$/);
  await screen.findByRole("button", { name: "Return control and continue" });
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "Return control and continue",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
  post.mockRejectedValueOnce(new Error("Cleanup unconfirmed"));
  await user.click(
    screen.getByRole("button", { name: "Return control and continue" }),
  );
  await screen.findByText("Cleanup unconfirmed");
  expect(
    post.mock.calls.filter(([path]) => path.endsWith("/resume")),
  ).toHaveLength(1);
});

it("waits for in-flight renewal before handback while disabling input immediately", async () => {
  const user = userEvent.setup();
  const timers = vi.spyOn(globalThis, "setTimeout");
  try {
    start(true);
    const grant = {
      assistance: { ...view, state: "human_control", epoch: 1 },
      mode: "control",
      websocket_url: "wss://desktop.example/",
      password: "private",
      expires_in_seconds: 60,
    };
    post.mockResolvedValueOnce(reply(grant));
    await user.click(
      await screen.findByRole("button", { name: "Get control" }),
    );
    await waitFor(() => expect(viewers).toHaveLength(1));
    act(() => viewers[0].dispatchEvent(new Event("connect")));
    let renewed!: (value: object) => void;
    post.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          renewed = resolve;
        }),
    );
    const timer = timers.mock.calls.find(([, delay]) => delay === 15000);
    expect(timer).toBeDefined();
    act(() => {
      (timer![0] as () => void)();
    });
    await user.click(
      screen.getByRole("button", { name: "Return control and continue" }),
    );
    expect(viewers[0].viewOnly).toBe(true);
    expect(viewers[0].disconnect).toHaveBeenCalled();
    expect(post.mock.calls.map(([path]) => path)).toEqual([
      "/api/v1/desktop-assistance/{assistance_id}/control",
      "/api/v1/desktop-assistance/{assistance_id}/control",
    ]);
    post.mockResolvedValueOnce(
      reply({
        ...view,
        state: "ready_to_resume",
        outcome: { outcome: "completed" },
      }),
    );
    post.mockResolvedValueOnce(reply({ id: "next" }));
    await act(async () => {
      renewed(reply(grant));
    });
    await waitFor(() =>
      expect(post.mock.calls.map(([path]) => path).slice(2)).toEqual([
        "/api/v1/desktop-assistance/{assistance_id}/complete",
        "/api/v1/runs/{run_id}/resume",
      ]),
    );
  } finally {
    timers.mockRestore();
  }
});

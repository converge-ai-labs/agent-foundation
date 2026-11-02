import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../../shared/api";
import { EnvironmentDesktop } from "./environment-desktop";

const { get, post, viewers, t } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  t: (key: string) => key,
  viewers: [] as Array<
    EventTarget & { viewOnly: boolean; disconnect: ReturnType<typeof vi.fn> }
  >,
}));
vi.mock("../../../auth/context", () => ({
  useClient: () => ({ workspace: () => ({ GET: get, POST: post }) }),
  revalidateSession: vi.fn(),
}));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws" } }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t, i18n: { resolvedLanguage: "en" } }),
}));
vi.mock("@novnc/novnc/lib/rfb", () => ({
  default: {
    default: class extends EventTarget {
      viewOnly = false;
      scaleViewport = false;
      disconnect = vi.fn(() => this.dispatchEvent(new Event("disconnect")));
      constructor() {
        super();
        viewers.push(this);
      }
    },
  },
}));
const automated: Schema["Control"] = {
  id: "control",
  version: 1,
  generation: 1,
  episode: 0,
  control: "automation",
  controller_id: null,
  controller_thread_id: null,
  expires_at: null,
  failure: null,
};
const human: Schema["Control"] = {
  ...automated,
  version: 3,
  episode: 1,
  control: "human",
  controller_id: "user",
  controller_thread_id: "thread",
  expires_at: "2099-01-01T00:00:00Z",
};
const key = ["desktop-control", "ws", "env"];
const reply = (value: object) => ({
  data: value,
  response: new Response(null, { headers: { ETag: '"control:3"' } }),
});
function grant(mode = "control", episode = 1, ttl = 60000) {
  return {
    mode,
    websocket_url: "wss://desktop.example/secret",
    password: "secret-password",
    generation: 1,
    episode,
    expires_at: new Date(Date.now() + ttl).toISOString(),
    viewer_id: mode === "view" ? "dview_stable" : null,
  };
}
function start(initial = human) {
  get.mockResolvedValue(reply(initial));
  post.mockImplementation(async (_path, { body }) =>
    reply(grant(body.mode, body.episode)),
  );
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrap = (id = "env") => (
    <QueryClientProvider client={cache}>
      <EnvironmentDesktop
        key={id}
        environmentId={id}
        threadId="thread"
        actorId="user"
      />
    </QueryClientProvider>
  );
  const result = render(wrap());
  return {
    ...result,
    cache,
    changeEnvironment: () => result.rerender(wrap("other")),
  };
}
async function observing() {
  await waitFor(() => expect(viewers).toHaveLength(1));
  act(() => viewers[0].dispatchEvent(new Event("connect")));
  expect(viewers[0].viewOnly).toBe(true);
}
async function connect(ttl = 60000) {
  await observing();
  post.mockResolvedValueOnce(reply(grant("control", 1, ttl)));
  fireEvent.click(screen.getByRole("button", { name: "Take control" }));
  await waitFor(() => expect(viewers).toHaveLength(2));
  expect(viewers[1].viewOnly).toBe(true);
  act(() => viewers[1].dispatchEvent(new Event("connect")));
  expect(viewers[1].viewOnly).toBe(false);
}
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.resetAllMocks();
  viewers.length = 0;
});

it("opens read-only automatically and unmounting only disconnects locally", async () => {
  const { unmount } = start(automated);
  await observing();
  expect(post.mock.calls[0][1].body).toMatchObject({
    mode: "view",
    generation: 1,
    episode: 0,
  });
  expect(screen.queryByRole("button", { name: "View desktop" })).toBeNull();
  unmount();
  expect(viewers[0].disconnect).toHaveBeenCalled();
  expect(post).toHaveBeenCalledTimes(1);
});

it("takes control in one action, waits for authorization, renews and returns without Run commands", async () => {
  const { cache } = start(automated);
  await observing();
  const draining = { ...human, control: "draining" as const };
  get.mockResolvedValue(reply(draining));
  post.mockResolvedValueOnce(reply(draining));
  fireEvent.click(screen.getByRole("button", { name: "Take control" }));
  await screen.findByText("Authorizing Computer control");
  expect(post).toHaveBeenCalledTimes(2);
  expect(post.mock.calls[1][1].body).toEqual({ thread_id: "thread" });
  get.mockResolvedValue(reply(human));
  act(() => cache.setQueryData(key, { value: human, etag: '"control:3"' }));
  await waitFor(() => expect(viewers).toHaveLength(2));
  act(() => viewers[1].dispatchEvent(new Event("connect")));
  expect(viewers[1].viewOnly).toBe(false);
  expect(JSON.stringify(cache.getQueryData(key))).not.toContain("secret");
  expect(cache.getMutationCache().getAll()).toHaveLength(0);
  post.mockResolvedValueOnce(reply({ ...human, version: 4 }));
  fireEvent.click(screen.getByRole("button", { name: "Renew control" }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(4));
  expect(post.mock.calls[3][1].body).toEqual({ generation: 1, episode: 1 });
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "Return control",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
  const returned = { ...automated, episode: 1, version: 5 };
  get.mockResolvedValue(reply(returned));
  post.mockResolvedValueOnce(reply(returned));
  fireEvent.click(screen.getByRole("button", { name: "Return control" }));
  await screen.findByText("Agent controls Computer");
  await waitFor(() => expect(viewers).toHaveLength(3));
  expect(viewers[1].viewOnly).toBe(true);
  expect(viewers[1].disconnect).toHaveBeenCalled();
  expect(post.mock.calls.at(-1)?.[1].body.mode).toBe("view");
  expect(
    post.mock.calls.every(([path]) => path.startsWith("/api/v1/environments/")),
  ).toBe(true);
  expect(
    screen.queryByText("Desktop disconnected. Open it again to reconnect."),
  ).toBeNull();
});

it.each(["retake", "return", "controller"])(
  "revokes input on %s and never restores it without an action",
  async (change) => {
    const { cache } = start();
    await connect();
    const updated = {
      ...human,
      ...(change === "retake"
        ? { episode: 2 }
        : change === "return"
          ? { control: "returning" }
          : { controller_id: "someone-else" }),
    };
    get.mockResolvedValue(reply(updated));
    act(() => cache.setQueryData(key, { value: updated }));
    await waitFor(() => expect(viewers[1].disconnect).toHaveBeenCalled());
    expect(viewers[1].viewOnly).toBe(true);
    await waitFor(() => expect(viewers).toHaveLength(3));
    expect(post.mock.calls.at(-1)?.[1].body.mode).toBe("view");
  },
);

it("discards late grants after changing Environment", async () => {
  const { changeEnvironment } = start();
  await observing();
  let resolve!: (value: object) => void;
  post.mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Take control" }));
  changeEnvironment();
  await waitFor(() => expect(viewers).toHaveLength(2));
  await act(async () => resolve(reply(grant())));
  expect(viewers).toHaveLength(2);
  expect(post.mock.calls.at(-1)?.[1].body.mode).toBe("view");
});

it("keeps input disabled when handback fails and exposes recovery", async () => {
  start();
  await connect();
  post.mockRejectedValueOnce(new Error("Cleanup could not be confirmed"));
  fireEvent.click(screen.getByRole("button", { name: "Return control" }));
  await screen.findByText("Cleanup could not be confirmed");
  expect(viewers[1].viewOnly).toBe(true);
  expect(viewers[1].disconnect).toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Return control" })).toBeTruthy();
});

it.each(["view", "control"])(
  "refreshes %s grants without reconnecting or renewing the human lease",
  async (mode) => {
    const { cache } = start();
    vi.useFakeTimers();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(viewers).toHaveLength(1);
    if (mode === "control") {
      fireEvent.click(screen.getByRole("button", { name: "Take control" }));
      await act(async () => {});
      expect(viewers).toHaveLength(2);
    }
    const count = viewers.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(45001);
    });
    expect(post.mock.calls.at(-1)?.[1].body).toMatchObject({
      mode,
      ...(mode === "view" ? { viewer_id: "dview_stable" } : {}),
    });
    expect(viewers).toHaveLength(count);
    expect(post.mock.calls.some(([path]) => path.endsWith("/renew"))).toBe(
      false,
    );
    expect(JSON.stringify(cache.getQueryData(key))).not.toContain("secret");
  },
);

it("expires input even when grant refresh never responds", async () => {
  start();
  await observing();
  vi.useFakeTimers();
  post.mockResolvedValueOnce(reply(grant("control", 1, 30000)));
  fireEvent.click(screen.getByRole("button", { name: "Take control" }));
  await act(async () => {});
  expect(viewers).toHaveLength(2);
  act(() => viewers[1].dispatchEvent(new Event("connect")));
  post.mockImplementationOnce(() => new Promise(() => {}));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(30001);
  });
  expect(viewers[1].viewOnly).toBe(true);
  expect(viewers[1].disconnect).toHaveBeenCalled();
  expect(
    screen.getByText("Desktop access expired. Open it again to reconnect."),
  ).toBeTruthy();
});

it.each(["disconnect", "securityfailure"])(
  "disables input on %s and retry starts read-only",
  async (event) => {
    start();
    await connect();
    act(() => viewers[1].dispatchEvent(new Event(event)));
    expect(viewers[1].viewOnly).toBe(true);
    expect(viewers[1].disconnect).toHaveBeenCalled();
    expect(screen.queryByText("Input enabled")).toBeNull();
    expect(
      screen.getByText("Desktop disconnected. Open it again to reconnect."),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Reload" }));
    await waitFor(() => expect(viewers).toHaveLength(3));
    expect(post.mock.calls.at(-1)?.[1].body.mode).toBe("view");
  },
);

it("disconnects on authority errors and unmount; recovery does not restore input", async () => {
  const { cache, unmount } = start();
  await connect();
  get.mockRejectedValueOnce(new Error("Authority unavailable"));
  await act(async () => {
    await cache.refetchQueries({ queryKey: key });
  });
  await screen.findByText("Authority unavailable");
  expect(viewers[1].viewOnly).toBe(true);
  expect(viewers[1].disconnect).toHaveBeenCalled();
  get.mockResolvedValue(reply(human));
  await act(async () => {
    await cache.refetchQueries({ queryKey: key });
  });
  await waitFor(() => expect(viewers).toHaveLength(3));
  expect(post.mock.calls.at(-1)?.[1].body.mode).toBe("view");
  unmount();
  expect(viewers[2].disconnect).toHaveBeenCalled();
});

it("does not enable a delayed connection after expiry", async () => {
  start();
  await observing();
  const now = Date.now();
  post.mockResolvedValueOnce(reply(grant("control", 1, 30000)));
  fireEvent.click(screen.getByRole("button", { name: "Take control" }));
  await waitFor(() => expect(viewers).toHaveLength(2));
  vi.spyOn(Date, "now").mockReturnValue(now + 30001);
  act(() => viewers[1].dispatchEvent(new Event("connect")));
  expect(viewers[1].viewOnly).toBe(true);
  expect(viewers[1].disconnect).toHaveBeenCalled();
});

it("invalidates pending control grants across another tab's return and retake", async () => {
  const { cache } = start();
  await observing();
  let resolve!: (value: object) => void;
  post.mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Take control" }));
  expect(post.mock.calls[1][1].body).toMatchObject({
    mode: "control",
    generation: 1,
    episode: 1,
  });
  const retaken = { ...human, episode: 2, version: 6 };
  get.mockResolvedValue(reply(retaken));
  act(() => cache.setQueryData(key, { value: retaken }));
  await waitFor(() => expect(viewers).toHaveLength(2));
  await act(async () => resolve(reply({ ...grant(), episode: 2 })));
  expect(viewers).toHaveLength(2);
  expect(post.mock.calls.at(-1)?.[1].body.mode).toBe("view");
  fireEvent.click(screen.getByRole("button", { name: "Take control" }));
  await waitFor(() => expect(viewers).toHaveLength(3));
  expect(post.mock.calls.at(-1)?.[1].body).toMatchObject({
    mode: "control",
    generation: 1,
    episode: 2,
  });
});

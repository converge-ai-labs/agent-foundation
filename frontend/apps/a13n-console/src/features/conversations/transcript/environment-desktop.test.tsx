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
import { StrictMode } from "react";
import userEvent from "@testing-library/user-event";
import type { Schema } from "../../../shared/api";
import { EnvironmentDesktop, ThreadDesktopPanel } from "./environment-desktop";

const { get, post, viewers, translation } = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  translation: { t: (key: string) => key },
  viewers: [] as Array<
    EventTarget & { viewOnly: boolean; disconnect: ReturnType<typeof vi.fn> }
  >,
}));
vi.mock("../../../auth/context", () => ({
  useClient: () => ({ workspace: () => ({ GET: get, POST: post }) }),
  revalidateSession: vi.fn(),
  useAuth: () => ({ data: { user: { value: { id: "user" } } } }),
}));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws" } }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: translation.t,
    i18n: { resolvedLanguage: "en" },
  }),
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
function start(initial = human, strict = false) {
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
  if (strict) cache.setQueryData(key, { value: initial, etag: '"control:3"' });
  const result = render(strict ? <StrictMode>{wrap()}</StrictMode> : wrap());
  return {
    ...result,
    cache,
    changeEnvironment: () => result.rerender(wrap("other")),
    refreshRender: () => result.rerender(wrap()),
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
  translation.t = (key: string) => key;
});

function panelMount(
  name: string,
  desktop_enabled: boolean,
): Schema["MountView"] {
  return {
    name,
    environment_id: name,
    session_binding: "default",
    working_directory: null,
    desktop_enabled,
    permission_ceiling: {
      operations: desktop_enabled ? [] : ["environment.computer.observe"],
    },
  };
}

function startPanel(items: Schema["MountView"][]) {
  let current = items;
  get.mockImplementation(async (path) =>
    reply(path.includes("/threads/") ? { items: current } : automated),
  );
  post.mockImplementation(async (_path, { body }) =>
    reply(grant(body.mode, body.episode)),
  );
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <ThreadDesktopPanel threadId="thread" onClose={() => {}} />
    </QueryClientProvider>,
  );
  return {
    cache,
    refresh: async (items: Schema["MountView"][]) => {
      current = items;
      await act(async () => {
        await cache.invalidateQueries();
      });
    },
  };
}

it("selects only desktop-enabled mounts independently of Agent permissions and falls back when removed", async () => {
  const ordinary = panelMount("files", false);
  const { refresh } = startPanel([
    ordinary,
    panelMount("desktop-a", true),
    panelMount("desktop-b", true),
  ]);
  await observing();
  expect(post.mock.calls[0][1].params.path.environment_id).toBe("desktop-a");
  const user = userEvent.setup();
  await user.click(screen.getByRole("combobox", { name: "Environment mount" }));
  expect(await screen.findByRole("option", { name: "desktop-b" })).toBeTruthy();
  expect(screen.queryByRole("option", { name: "files" })).toBeNull();
  await user.click(screen.getByRole("option", { name: "desktop-b" }));
  await waitFor(() => expect(viewers).toHaveLength(2));
  expect(viewers[0].disconnect).toHaveBeenCalled();
  expect(post.mock.calls[1][1].params.path.environment_id).toBe("desktop-b");
  await refresh([ordinary, panelMount("desktop-a", true)]);
  await waitFor(() => expect(viewers).toHaveLength(3));
  expect(viewers[1].disconnect).toHaveBeenCalled();
  expect(post.mock.calls[2][1].params.path.environment_id).toBe("desktop-a");
  await refresh([ordinary]);
  await screen.findByText("No desktop-enabled environments mounted.");
  expect(viewers[2].disconnect).toHaveBeenCalled();
  expect(screen.queryByRole("combobox")).toBeNull();
});

it("shows a mount query failure without misreporting an empty desktop list", async () => {
  const { cache } = startPanel([]);
  await screen.findByText("No desktop-enabled environments mounted.");
  get.mockRejectedValue(new Error("Mount listing failed"));
  await act(async () => {
    await cache.invalidateQueries();
  });
  await screen.findByText("Mount listing failed");
  expect(
    screen.queryByText("No desktop-enabled environments mounted."),
  ).toBeNull();
  expect(post).not.toHaveBeenCalled();
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

it("opens a viewer after StrictMode replays setup with cached authority", async () => {
  start(automated, true);
  await observing();
  expect(post).toHaveBeenCalledTimes(2);
  expect(
    post.mock.calls.every(([, request]) => request.body.mode === "view"),
  ).toBe(true);
});

it("does not refresh a control grant capped by the human lease", async () => {
  const expires = new Date(Date.now() + 30000).toISOString();
  const { cache } = start({ ...human, expires_at: expires });
  await observing();
  vi.useFakeTimers();
  post.mockResolvedValueOnce(reply({ ...grant(), expires_at: expires }));
  fireEvent.click(screen.getByRole("button", { name: "Take control" }));
  await act(async () => {});
  act(() => viewers[1].dispatchEvent(new Event("connect")));
  const requests = post.mock.calls.length;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(20000);
  });
  expect(post).toHaveBeenCalledTimes(requests);
  expect(viewers[1].viewOnly).toBe(false);
  // Explicit lease renewal makes the short guest grant refreshable again.
  const renewed = {
    ...human,
    expires_at: new Date(Date.now() + 60000).toISOString(),
  };
  get.mockResolvedValue(reply(renewed));
  act(() => cache.setQueryData(key, { value: renewed, etag: '"control:4"' }));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1001);
  });
  expect(post).toHaveBeenCalledTimes(requests + 1);
  expect(viewers).toHaveLength(2);
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

it("keeps a live connection when the display language changes and localizes later failures", async () => {
  const { refreshRender } = start();
  await connect();
  translation.t = (key) =>
    key === "Desktop disconnected. Open it again to reconnect."
      ? "桌面已断开，请重新连接。"
      : key;
  refreshRender();
  expect(viewers).toHaveLength(2);
  expect(viewers[1].disconnect).not.toHaveBeenCalled();
  expect(viewers[1].viewOnly).toBe(false);
  act(() => viewers[1].dispatchEvent(new Event("disconnect")));
  await screen.findByText("桌面已断开，请重新连接。");
  expect(viewers[1].viewOnly).toBe(true);
});

it("shows a failed takeover and clears it after an explicit successful retry", async () => {
  start(automated);
  await observing();
  const failed = { ...automated, episode: 1, failure: "desktop_grant_failed" };
  get.mockResolvedValue(reply(failed));
  post.mockResolvedValueOnce(reply(failed));
  fireEvent.click(screen.getByRole("button", { name: "Take control" }));
  await screen.findByText("Could not take control. You can try again.");
  await waitFor(() => expect(viewers).toHaveLength(2));
  expect(post.mock.calls.at(-1)?.[1].body.mode).toBe("view");
  expect(screen.queryByRole("button", { name: "Return control" })).toBeNull();
  const retried = { ...human, episode: 2 };
  get.mockResolvedValue(reply(retried));
  post.mockResolvedValueOnce(reply(retried));
  fireEvent.click(screen.getByRole("button", { name: "Take control" }));
  await screen.findByText("You control Computer");
  await waitFor(() => expect(viewers).toHaveLength(3));
  expect(
    screen.queryByText("Could not take control. You can try again."),
  ).toBeNull();
  expect(post.mock.calls.at(-1)?.[1].body).toMatchObject({
    mode: "control",
    episode: 2,
  });
});

it("shows pending cleanup without offering takeover and clears the warning when it completes", async () => {
  const { cache } = start();
  await connect();
  const returning = {
    ...human,
    control: "returning",
    failure: "desktop_revoke_failed",
  };
  get.mockResolvedValue(reply(returning));
  post.mockResolvedValueOnce(reply(returning));
  fireEvent.click(screen.getByRole("button", { name: "Return control" }));
  await screen.findByText(
    "Desktop control failed. Returning control automatically.",
  );
  expect(screen.queryByRole("button", { name: "Take control" })).toBeNull();
  expect(viewers[1].viewOnly).toBe(true);
  expect(viewers[1].disconnect).toHaveBeenCalled();
  const returned = { ...automated, episode: 1 };
  get.mockResolvedValue(reply(returned));
  act(() => cache.setQueryData(key, { value: returned }));
  await screen.findByRole("button", { name: "Take control" });
  expect(
    screen.queryByText(
      "Desktop control failed. Returning control automatically.",
    ),
  ).toBeNull();
});

it("keeps a post-dispatch takeover failure visible through cleanup and offers retry only afterward", async () => {
  const { cache } = start(automated);
  await observing();
  const returning = {
    ...human,
    control: "returning",
    failure: "desktop_grant_failed",
  };
  get.mockResolvedValue(reply(returning));
  post.mockResolvedValueOnce(reply(returning));
  fireEvent.click(screen.getByRole("button", { name: "Take control" }));
  await screen.findByText(
    "Desktop control failed. Returning control automatically.",
  );
  expect(screen.queryByRole("button", { name: "Take control" })).toBeNull();
  const returned = { ...automated, episode: 1, failure: returning.failure };
  get.mockResolvedValue(reply(returned));
  act(() => cache.setQueryData(key, { value: returned }));
  await screen.findByText("Could not take control. You can try again.");
  expect(
    screen
      .getByRole("button", { name: "Take control" })
      .hasAttribute("disabled"),
  ).toBe(false);
});

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

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
function grant(
  mode = "control",
  expires_at = new Date(Date.now() + 60000).toISOString(),
) {
  return {
    mode,
    websocket_url: "wss://desktop.example/secret",
    password: "secret-password",
    generation: 1,
    episode: 1,
    expires_at,
  };
}
function start(initial = human) {
  get.mockResolvedValue(reply(initial));
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
async function connect() {
  post.mockResolvedValueOnce(reply(grant()));
  fireEvent.click(await screen.findByRole("button", { name: "Connect input" }));
  await waitFor(() => expect(viewers).toHaveLength(1));
  expect(viewers[0].viewOnly).toBe(true);
  act(() => {
    viewers[0].dispatchEvent(new Event("connect"));
  });
  expect(viewers[0].viewOnly).toBe(false);
}
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.resetAllMocks();
  viewers.length = 0;
});

it("takes over independently, connects only after settlement, renews and returns without Run commands", async () => {
  const user = userEvent.setup();
  const { cache } = start(automated);
  await screen.findByRole("button", { name: "Take control" });
  expect(post).not.toHaveBeenCalled();
  const draining = { ...human, control: "draining" as const };
  get.mockResolvedValue(reply(draining));
  post.mockResolvedValueOnce(reply(draining));
  await user.click(screen.getByRole("button", { name: "Take control" }));
  await screen.findByText("Waiting for Computer actions to settle");
  expect(screen.queryByRole("button", { name: "Connect input" })).toBeNull();
  expect(post.mock.calls[0][1].body).toEqual({ thread_id: "thread" });
  get.mockResolvedValue(reply(human));
  act(() => {
    cache.setQueryData(key, { value: human, etag: '"control:3"' });
  });
  await connect();
  expect(JSON.stringify(cache.getQueryData(key))).not.toContain("secret");
  expect(cache.getMutationCache().getAll()).toHaveLength(0);
  post.mockResolvedValueOnce(reply({ ...human, version: 4 }));
  await user.click(screen.getByRole("button", { name: "Renew control" }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(3));
  expect(post.mock.calls[2][1].body).toEqual({ generation: 1, episode: 1 });
  get.mockResolvedValue(reply({ ...automated, episode: 1, version: 5 }));
  post.mockResolvedValueOnce(reply({ ...automated, episode: 1, version: 5 }));
  await user.click(screen.getByRole("button", { name: "Return control" }));
  expect(viewers[0].viewOnly).toBe(true);
  expect(viewers[0].disconnect).toHaveBeenCalled();
  await screen.findByText("Agent controls Computer");
  expect(
    post.mock.calls.every(([path]) => path.startsWith("/api/v1/environments/")),
  ).toBe(true);
  expect(
    screen.queryByText("Desktop disconnected. Open it again to reconnect."),
  ).toBeNull();
});

it("views read-only without acquiring control and closing only disconnects locally", async () => {
  start();
  post.mockResolvedValueOnce(reply(grant("view")));
  fireEvent.click(await screen.findByRole("button", { name: "View desktop" }));
  await waitFor(() => expect(viewers).toHaveLength(1));
  act(() => {
    viewers[0].dispatchEvent(new Event("connect"));
  });
  expect(viewers[0].viewOnly).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Close viewer" }));
  expect(viewers[0].disconnect).toHaveBeenCalled();
  expect(post).toHaveBeenCalledTimes(1);
  expect(screen.getByText("You control Computer")).toBeTruthy();
});

it.each(["retake", "return", "controller"])(
  "disables input when metadata reports %s",
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
    act(() => {
      cache.setQueryData(key, { value: updated });
    });
    await waitFor(() => expect(viewers[0].disconnect).toHaveBeenCalled());
    expect(viewers[0].viewOnly).toBe(true);
  },
);

it("does not attach a late viewer after closing or changing Environment", async () => {
  const { changeEnvironment } = start();
  let resolve!: (value: object) => void;
  post.mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  fireEvent.click(await screen.findByRole("button", { name: "Connect input" }));
  fireEvent.click(screen.getByRole("button", { name: "Close viewer" }));
  changeEnvironment();
  await act(async () => {
    resolve(reply(grant()));
  });
  expect(viewers).toHaveLength(0);
});

it("keeps input disabled when handback fails and exposes explicit recovery", async () => {
  start();
  await connect();
  post.mockRejectedValueOnce(new Error("Cleanup could not be confirmed"));
  fireEvent.click(screen.getByRole("button", { name: "Return control" }));
  await screen.findByText("Cleanup could not be confirmed");
  expect(viewers[0].viewOnly).toBe(true);
  expect(viewers[0].disconnect).toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "Return control" })).toBeTruthy();
});

it("refreshes a short viewer grant without reconnecting or renewing the human lease", async () => {
  const { cache } = start();
  await connect();
  vi.useFakeTimers();
  post.mockResolvedValue(
    reply(grant("control", new Date(Date.now() + 60000).toISOString())),
  );
  // A fresh connection arms the short-lived grant timer.
  fireEvent.click(screen.getByRole("button", { name: "Close viewer" }));
  fireEvent.click(screen.getByRole("button", { name: "Connect input" }));
  await act(async () => {});
  const count = viewers.length;
  post.mockImplementation(async () =>
    reply(grant("control", new Date(Date.now() + 60000).toISOString())),
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(45001);
  });
  expect(
    post.mock.calls.filter(([path]) => path.endsWith("/desktop-viewer")),
  ).toHaveLength(3);
  expect(viewers).toHaveLength(count);
  expect(post.mock.calls.some(([path]) => path.endsWith("/renew"))).toBe(false);
  expect(JSON.stringify(cache.getQueryData(key))).not.toContain("secret");
});

it("closes at lease expiry even when the next viewer refresh never responds", async () => {
  start();
  await screen.findByRole("button", { name: "Connect input" });
  vi.useFakeTimers();
  post.mockResolvedValueOnce(
    reply(grant("control", new Date(Date.now() + 30000).toISOString())),
  );
  fireEvent.click(screen.getByRole("button", { name: "Connect input" }));
  await act(async () => {});
  expect(viewers).toHaveLength(1);
  act(() => {
    viewers[0].dispatchEvent(new Event("connect"));
  });
  post.mockImplementationOnce(() => new Promise(() => {}));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(30001);
  });
  expect(viewers[0].viewOnly).toBe(true);
  expect(viewers[0].disconnect).toHaveBeenCalled();
  expect(
    screen.getByText("Desktop access expired. Open it again to reconnect."),
  ).toBeTruthy();
});

it.each(["disconnect", "securityfailure"])(
  "clears credentials and disables input on %s",
  async (event) => {
    start();
    await connect();
    act(() => {
      viewers[0].dispatchEvent(new Event(event));
    });
    expect(viewers[0].viewOnly).toBe(true);
    expect(viewers[0].disconnect).toHaveBeenCalled();
    expect(screen.queryByText("Input enabled")).toBeNull();
    expect(
      screen.getByText("Desktop disconnected. Open it again to reconnect."),
    ).toBeTruthy();
  },
);

it("disconnects when authority refresh fails and on unmount", async () => {
  const { cache, unmount } = start();
  await connect();
  get.mockRejectedValueOnce(new Error("Authority unavailable"));
  await act(async () => {
    await cache.refetchQueries({ queryKey: key });
  });
  await screen.findByText("Authority unavailable");
  expect(viewers[0].viewOnly).toBe(true);
  expect(viewers[0].disconnect).toHaveBeenCalled();
  get.mockResolvedValue(reply(human));
  await act(async () => {
    await cache.refetchQueries({ queryKey: key });
  });
  post.mockResolvedValueOnce(reply(grant()));
  fireEvent.click(screen.getByRole("button", { name: "Connect input" }));
  await waitFor(() => expect(viewers).toHaveLength(2));
  unmount();
  expect(viewers[1].viewOnly).toBe(true);
  expect(viewers[1].disconnect).toHaveBeenCalled();
});

it("does not enable a delayed connection after its grant has expired", async () => {
  start();
  await screen.findByRole("button", { name: "Connect input" });
  const now = Date.now();
  post.mockResolvedValueOnce(
    reply(grant("control", new Date(now + 30000).toISOString())),
  );
  fireEvent.click(screen.getByRole("button", { name: "Connect input" }));
  await waitFor(() => expect(viewers).toHaveLength(1));
  vi.spyOn(Date, "now").mockReturnValue(now + 30001);
  act(() => {
    viewers[0].dispatchEvent(new Event("connect"));
  });
  expect(viewers[0].viewOnly).toBe(true);
  expect(viewers[0].disconnect).toHaveBeenCalled();
  vi.restoreAllMocks();
});

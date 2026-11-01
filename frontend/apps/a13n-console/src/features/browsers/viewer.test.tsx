import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { BrowserViewer } from "./viewer";

const { post, client, t, viewers } = vi.hoisted(() => {
  const post = vi.fn();
  return {
    post,
    client: { workspace: () => ({ POST: post }) },
    t: (value: string) => value,
    viewers: [] as Array<
      EventTarget & {
        viewOnly: boolean;
        disconnect: ReturnType<typeof vi.fn>;
        url: string;
        options: unknown;
      }
    >,
  };
});
vi.mock("../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws" } }),
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t }) }));
vi.mock("@novnc/novnc/lib/rfb", () => ({
  default: {
    default: class extends EventTarget {
      viewOnly = false;
      scaleViewport = false;
      disconnect = vi.fn();
      constructor(
        _screen: HTMLElement,
        public url: string,
        public options: unknown,
      ) {
        super();
        viewers.push(this);
      }
    },
  },
}));
const reply = (value: unknown) => ({ data: value, response: new Response() });
function grant(seconds = 60) {
  return {
    protocol: "vnc",
    url: "wss://console.test/api/v1/browsers/br/viewer/connect",
    password: "private-password",
    token: "private-token",
    generation: 2,
    episode: 3,
    expires_at: new Date(Date.now() + seconds * 1000).toISOString(),
  };
}
function start() {
  const cache = new QueryClient();
  return {
    cache,
    ...render(
      <QueryClientProvider client={cache}>
        <BrowserViewer id="br" generation={2} episode={3} />
      </QueryClientProvider>,
    ),
  };
}
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
  viewers.length = 0;
});

it("keeps credentials out of caches and enables input only on the mounted connection", async () => {
  post.mockResolvedValue(reply(grant()));
  const { cache, unmount } = start();
  await waitFor(() => expect(viewers).toHaveLength(1));
  const viewer = viewers[0];
  expect(viewer.viewOnly).toBe(true);
  expect(viewer.url).not.toContain("private");
  expect(viewer.options).toEqual({
    credentials: { password: "private-password" },
    shared: true,
    wsProtocols: ["binary", "a13n.browser.private-token"],
  });
  act(() => viewer.dispatchEvent(new Event("connect")));
  expect(viewer.viewOnly).toBe(false);
  expect(cache.getQueryCache().getAll()).toEqual([]);
  expect(cache.getMutationCache().getAll()).toEqual([]);
  unmount();
  expect(viewer.viewOnly).toBe(true);
  expect(viewer.disconnect).toHaveBeenCalledOnce();
  expect(post).toHaveBeenCalledTimes(1); // Unmount neither stops nor hands back the Browser.
});

it("expires input at the published deadline while a renewal is still unresolved", async () => {
  vi.useFakeTimers();
  post
    .mockResolvedValueOnce(reply(grant(20)))
    .mockImplementation(() => new Promise(() => {}));
  start();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(viewers).toHaveLength(1);
  act(() => viewers[0].dispatchEvent(new Event("connect")));
  await act(async () => {
    await vi.advanceTimersByTimeAsync(15000);
  });
  expect(post).toHaveBeenLastCalledWith(
    "/api/v1/browsers/{browser_id}/renew",
    expect.objectContaining({ body: { generation: 2, episode: 3 } }),
  );
  await act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
  expect(viewers[0].viewOnly).toBe(true);
  expect(viewers[0].disconnect).toHaveBeenCalledOnce();
  expect(
    screen.getByText("Browser access expired. Reopen the viewer to reconnect."),
  ).toBeTruthy();
});

it("extends input only after reading a renewed supplier grant", async () => {
  vi.useFakeTimers();
  post
    .mockResolvedValueOnce(reply(grant(20)))
    .mockResolvedValueOnce(reply({}))
    .mockImplementation(() => Promise.resolve(reply(grant(60))));
  const { unmount } = start();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(20000);
  });
  expect(post.mock.calls.map(([path]) => path.split("/").at(-1))).toEqual([
    "viewer",
    "renew",
    "viewer",
  ]);
  expect(viewers[0].disconnect).not.toHaveBeenCalled();
  unmount();
  expect(viewers[0].disconnect).toHaveBeenCalledOnce();
});

it.each([{ generation: 1 }, { episode: 2 }])(
  "refuses stale grant %j",
  async (stale) => {
    post.mockResolvedValue(reply({ ...grant(), ...stale }));
    start();
    await screen.findByText(
      "Browser control changed. Reopen the viewer to reconnect.",
    );
    expect(viewers).toHaveLength(0);
  },
);

it("does not attach a late grant after the viewer closes", async () => {
  let respond!: (value: unknown) => void;
  post.mockImplementation(
    () =>
      new Promise((resolve) => {
        respond = resolve;
      }),
  );
  const { unmount } = start();
  unmount();
  await act(async () => {
    respond(reply(grant()));
  });
  expect(viewers).toHaveLength(0);
});

it("retains iframe supplier compatibility without caching its URL", async () => {
  post.mockResolvedValue(
    reply({
      ...grant(),
      protocol: "iframe",
      url: "https://viewer.test/private",
    }),
  );
  const { cache, unmount } = start();
  expect((await screen.findByTitle("Browser viewer")).getAttribute("src")).toBe(
    "https://viewer.test/private",
  );
  expect(cache.getQueryCache().getAll()).toEqual([]);
  expect(viewers).toHaveLength(0);
  unmount();
});

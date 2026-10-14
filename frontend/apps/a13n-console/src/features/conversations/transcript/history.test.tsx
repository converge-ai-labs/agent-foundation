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
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { useRef, type ReactNode } from "react";
import type { DisplayItem } from "a13n-ui/display";
import { createClient, type Client } from "../../../service-client";
import { fixtureRun, fixtureThread } from "./fixture";
import { HistoryTranscript } from "./history";
import { useTranscriptScroll } from "./use-transcript-scroll";

let client: Client;
let displayItems: DisplayItem[] = [];
const thread = fixtureThread({ id: "thread", session_id: "session" });
vi.mock("../../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "workspace" },
    basePath: "/workspace/demo",
    can: () => true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
vi.mock("../agents/queries", () => ({ useAgent: () => ({ data: null }) }));
vi.mock("./user-message", () => ({
  RequestContent: () => null,
  UserMessage: () => null,
  requestLabel: () => "You",
}));
vi.mock("./assistant-message", () => ({
  AgentTurn: ({
    blocks,
    children,
  }: {
    blocks: { id: string; entry?: { text: string } }[];
    children?: ReactNode;
  }) => (
    <>
      {blocks.map((block) => (
        <p key={block.id} data-message-id={block.id}>
          {block.entry?.text}
        </p>
      ))}
      {children}
    </>
  ),
  CopyReply: () => null,
}));

/** The reader's viewport, which the sentinel and the ancestors live in. */
class StageObserver {
  static created: StageObserver[] = [];
  targets: Element[] = [];
  constructor(
    private callback: IntersectionObserverCallback,
    readonly options: IntersectionObserverInit,
  ) {
    StageObserver.created.push(this);
  }
  observe(target: Element) {
    this.targets.push(target);
  }
  unobserve() {}
  disconnect() {
    this.targets = [];
  }
  /** The sentinel has scrolled into view. */
  reach() {
    const stage = this.targets[0]?.closest<HTMLElement>("[data-session-stage]");
    if (stage) {
      fireEvent.wheel(stage, { deltaY: -200 });
      stage.scrollTop = 0;
      fireEvent.scroll(stage);
    }
    this.intersect();
  }
  /** Layout alone must not request history. */
  intersect() {
    this.callback(
      this.targets.map(
        (target) =>
          ({ target, isIntersecting: true }) as IntersectionObserverEntry,
      ),
      this as unknown as IntersectionObserver,
    );
  }
}

beforeEach(() => {
  displayItems = [];
  StageObserver.created = [];
  vi.stubGlobal("IntersectionObserver", StageObserver);
  const original = HTMLElement.prototype.getBoundingClientRect;
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
    function (this: HTMLElement) {
      if (!this.hasAttribute("data-run")) return original.call(this);
      const stage = this.closest<HTMLElement>("[data-session-stage]")!;
      const index = [...stage.querySelectorAll("[data-run]")].indexOf(this);
      return new DOMRect(
        0,
        index * 400 - stage.scrollTop,
        700,
        this.dataset.run === "current" ? 2000 : 400,
      );
    },
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  client?.close();
});

function stage(
  level: "chat" | "debug",
  prepare?: (cache: QueryClient) => void,
) {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  prepare?.(cache);
  function Transcript({ level }: { level: "chat" | "debug" }) {
    const content = useRef<HTMLDivElement>(null);
    const scroll = useTranscriptScroll(content, level);
    return (
      <div
        data-session-stage
        ref={(node) => {
          if (!node) return;
          Object.defineProperties(node, {
            scrollHeight: {
              configurable: true,
              get: () =>
                2000 + (node.querySelectorAll("[data-run]").length - 1) * 400,
            },
            clientHeight: { configurable: true, value: 500 },
          });
          node.scrollTo = (options) => {
            if (typeof options === "object")
              node.scrollTop = Math.min(
                options.top ?? 0,
                node.scrollHeight - node.clientHeight,
              );
          };
        }}
      >
        <div ref={content}>
          <HistoryTranscript
            runId="current"
            thread={thread}
            level={level}
            preservePosition={scroll.preservePosition}
            jumpToDock={scroll.jumpToLatest}
          />
          <div data-run="current">Current run</div>
        </div>
      </div>
    );
  }
  const view = render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <Transcript level={level} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return {
    cache,
    switchLevel(next: "chat" | "debug") {
      view.rerender(
        <QueryClientProvider client={cache}>
          <MemoryRouter>
            <Transcript level={next} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    },
    viewport: view.container.querySelector<HTMLElement>(
      "[data-session-stage]",
    )!,
  };
}

/** Ancestors a test marks as failed; every other one completed. */
let failed = new Set<string>();

/** A Run of the fixture Thread, as the lineage and the Run read serve it. */
const ancestor = (id: string) =>
  fixtureRun({
    id,
    thread_id: "thread",
    session_id: "session",
    agent_id: "agent",
    input: { content: [{ type: "text", text: "Earlier request" }] },
    ...(failed.has(id) && {
      status: "failed",
      failure: { code: "agent_run_failed", message: "Agent failed." },
    }),
  });

/** The lineage in pages, nearest first, as the Service pages it. */
function ancestorService(
  requests: URL[],
  pages: string[][] = [["current", "parent", "grandparent"]],
  beforeRead?: (url: URL) => Promise<void>,
) {
  return createClient({
    maxReadRetries: 0,
    baseUrl: "https://test.invalid",
    auth: { type: "session" },
    fetch: async (input) => {
      const url = new URL((input as Request).url);
      requests.push(url);
      await beforeRead?.(url);
      if (url.pathname.endsWith("/lineage")) {
        const index = Number(url.searchParams.get("cursor") ?? 0);
        return Response.json({
          items: pages[index]!.map(ancestor),
          next_cursor: index + 1 < pages.length ? String(index + 1) : null,
        });
      }
      if (url.pathname.endsWith("/threads/thread/runs"))
        return Response.json({ items: [], next_cursor: null });
      if (url.pathname.endsWith("/attempts"))
        return Response.json({ items: [] });
      if (url.pathname.endsWith("/usage"))
        return Response.json({ models: [], runs: [] });
      if (url.pathname.endsWith("/items")) {
        const run = url.pathname.split("/").at(-2)!;
        return Response.json({
          run: ancestor(run),
          position: "2-0",
          complete: true,
          baseline: true,
          items: [
            {
              id: run,
              ordinal: 1,
              kind: "text_message",
              state: "completed",
              first_stream_id: "2-0",
              last_stream_id: "2-0",
              started_at: "2026-09-20T10:00:01.000Z",
              ended_at: "2026-09-20T10:00:02.000Z",
              content: { role: "assistant", text: `Latest ${run} message` },
            },
          ],
        });
      }
      return Response.json(ancestor(url.pathname.split("/").at(-1)!));
    },
  });
}

const recent = ["recent-1", "recent-2", "recent-3", "recent-4", "recent-5"];

async function readEarlier() {
  await waitFor(() =>
    expect(StageObserver.created.at(-1)?.targets.length).toBe(1),
  );
  act(() => StageObserver.created.at(-1)!.reach());
}

it("opens Chat with the selected Run and loads only a bounded batch on upward scrolling", async () => {
  const requests: URL[] = [];
  client = ancestorService(requests, [["current", ...recent, "parent"]]);
  const { cache, viewport } = stage("chat");
  await waitFor(() => expect(StageObserver.created).toHaveLength(1));
  expect(
    requests.filter((url) => url.pathname.endsWith("/items")),
  ).toHaveLength(0);
  expect(viewport.querySelectorAll("[data-run]")).toHaveLength(1);
  await readEarlier();
  await screen.findByText("Latest recent-5 message");
  expect(
    [...viewport.querySelectorAll("[data-run]")].map((run) =>
      run.getAttribute("data-run"),
    ),
  ).toEqual([...recent].reverse().concat("current"));
  expect(requests.some((url) => url.pathname.includes("/parent"))).toBe(false);
  expect(
    screen.getByRole("button", { name: "Load earlier messages" }),
  ).not.toHaveProperty("disabled", true);
  expect(StageObserver.created[0]!.options.rootMargin).toBe("500px 0px 0px");
  cache.clear();
});

it("says an earlier Chat run stopped short, and leaves resubmitting to the latest", async () => {
  failed = new Set(["recent-2"]);
  client = ancestorService([], [["current", ...recent, "parent"]]);
  const { cache } = stage("chat");
  await readEarlier();
  await screen.findByText("Latest recent-5 message");
  expect(await screen.findAllByText("The run could not finish")).toHaveLength(
    1,
  );
  expect(screen.getByText("Error details")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Resubmit" })).toBeNull();
  failed = new Set();
  cache.clear();
});

it("prepares another batch near the top and preserves the visible message", async () => {
  const requests: URL[] = [];
  client = ancestorService(requests, [
    ["current", ...recent, "parent", "grandparent"],
  ]);
  const { cache, viewport } = stage("chat");
  await readEarlier();
  await screen.findByText("Latest recent-5 message");
  await readEarlier();
  const anchor = viewport.querySelector('[data-run="recent-5"]')!;
  const top = anchor.getBoundingClientRect().top;
  expect(viewport.querySelector('[data-slot="skeleton"]')).toBeNull();
  await screen.findByText("Latest grandparent message");
  expect(anchor.getBoundingClientRect().top).toBe(top);
  expect(viewport.scrollTop).toBe(800);
  expect(
    screen.queryByRole("button", { name: "Load earlier messages" }),
  ).toBeNull();
  cache.clear();
});

it("reads the next lineage page only when more than the initial batch is requested", async () => {
  const requests: URL[] = [];
  client = ancestorService(requests, [["current", ...recent], ["parent"]]);
  const { cache } = stage("chat");
  const lineage = () =>
    requests.filter((url) => url.pathname.endsWith("/lineage"));
  await readEarlier();
  await screen.findByText("Latest recent-5 message");
  expect(lineage()).toHaveLength(1);
  // The visible control also works without an intersection callback.
  fireEvent.click(
    screen.getByRole("button", { name: "Load earlier messages" }),
  );
  await screen.findByText("Latest parent message");
  expect(lineage().map((url) => url.searchParams.get("cursor"))).toEqual([
    null,
    "1",
  ]);
  cache.clear();
});

it("waits for delayed lineage before mounting prepared history", async () => {
  let release!: () => void;
  const waiting = new Promise<void>((resolve) => {
    release = resolve;
  });
  const requests: URL[] = [];
  client = ancestorService(requests, undefined, async (url) => {
    if (url.pathname.endsWith("/lineage")) await waiting;
  });
  const { viewport, cache } = stage("chat");
  expect(
    screen.queryByRole("button", { name: "Load earlier messages" }),
  ).toBeNull();
  expect(viewport.querySelectorAll("[data-run]")).toHaveLength(1);
  release();
  await readEarlier();
  await screen.findByText("Latest grandparent message");
  expect(viewport.querySelector('[data-slot="skeleton"]')).toBeNull();
  expect(viewport.querySelectorAll("[data-run]")).toHaveLength(3);
  cache.clear();
});

it("restores the reader's current position after a slow history read, without intermediate skeletons", async () => {
  let release!: () => void;
  const waiting = new Promise<void>((resolve) => {
    release = resolve;
  });
  const requests: URL[] = [];
  client = ancestorService(
    requests,
    [["current", ...recent, "parent"]],
    async (url) => {
      if (url.pathname.endsWith("/parent/items")) await waiting;
    },
  );
  const { viewport, cache } = stage("chat");
  await readEarlier();
  await screen.findByText("Latest recent-5 message");
  await readEarlier();
  await waitFor(() =>
    expect(requests.some((url) => url.pathname.endsWith("/parent/items"))).toBe(
      true,
    ),
  );
  viewport.scrollTop = 150;
  fireEvent.scroll(viewport);
  const anchor = viewport.querySelector('[data-run="recent-5"]')!;
  const top = anchor.getBoundingClientRect().top;
  expect(viewport.querySelector('[data-slot="skeleton"]')).toBeNull();
  release();
  await screen.findByText("Latest parent message");
  expect(anchor.getBoundingClientRect().top).toBe(top);
  expect(viewport.scrollTop).toBe(550);
  expect(
    requests.some((url) => url.pathname.endsWith("/threads/thread/runs")),
  ).toBe(false);
  cache.clear();
});

it("preserves the same anchor when older history is already cached", async () => {
  const requests: URL[] = [];
  client = ancestorService(requests, [["current", ...recent, "parent"]]);
  const { viewport, cache } = stage("chat", (cache) => {
    cache.setQueryData(
      ["conversations", "workspace", "run", "parent"],
      ancestor("parent"),
    );
    cache.setQueryData(["conversations", "workspace", "items", "parent"], {
      run: ancestor("parent"),
      items: [],
      complete: true,
      position: "1-0",
    });
  });
  await readEarlier();
  await screen.findByText("Latest recent-5 message");
  await readEarlier();
  await waitFor(() =>
    expect(viewport.querySelector('[data-run="parent"]')).not.toBeNull(),
  );
  expect(
    viewport.querySelector('[data-run="recent-5"]')!.getBoundingClientRect()
      .top,
  ).toBe(0);
  expect(viewport.scrollTop).toBe(400);
  expect(requests.some((url) => url.pathname.includes("/parent"))).toBe(false);
  cache.clear();
});

it("reveals an ancestor as a debug section that reads its own display", async () => {
  const requests: URL[] = [];
  client = createClient({
    baseUrl: "https://test.invalid",
    auth: { type: "session" },
    fetch: async (input) => {
      const url = new URL((input as Request).url);
      requests.push(url);
      if (url.pathname.endsWith("/lineage"))
        return Response.json({
          items: ["current", "parent"].map(ancestor),
          next_cursor: null,
        });
      if (url.pathname.endsWith("/runs"))
        return Response.json({ items: [], next_cursor: null });
      if (url.pathname.endsWith("/threads"))
        return Response.json({ items: [], next_cursor: null });
      if (url.pathname.endsWith("/items"))
        return Response.json({
          run: ancestor("parent"),
          baseline: true,
          complete: true,
          position: "2-1",
          items: displayItems,
        });
      if (url.pathname.endsWith("/attempts"))
        return Response.json({ items: [] });
      if (url.pathname.endsWith("/usage"))
        return Response.json({ models: [], runs: [] });
      return Response.json({ ...ancestor("parent"), lineage: "root" });
    },
  });
  const { cache } = stage("debug");
  fireEvent.click(
    await screen.findByRole("button", { name: "Load earlier runs" }),
  );
  expect(await screen.findByText("Run")).toBeTruthy();
  expect(screen.getByText("state.completed")).toBeTruthy();
  expect(
    requests.filter((url) => url.pathname.endsWith("/parent/items")),
  ).toHaveLength(1);
  cache.clear();
});

it.each(["reply", "reasoning", "tool"] as const)(
  "expands a saved %s from an earlier Debug run using that run's content reference",
  async (kind) => {
    const field = kind === "tool" ? "result" : "text";
    const value =
      kind === "tool" ? { answer: "Full saved result" } : "Full saved message";
    displayItems = [
      {
        id: "itm_saved",
        ordinal: 1,
        kind:
          kind === "tool"
            ? "tool_call"
            : kind === "reasoning"
              ? "reasoning_message"
              : "text_message",
        state: "completed",
        first_stream_id: "2-0",
        last_stream_id: "2-1",
        started_at: "2026-09-20T10:00:01.000Z",
        content:
          kind === "tool"
            ? { toolCallName: "read_file", arguments: "{}" }
            : { role: "assistant", text: "Saved preview" },
        content_refs: {
          [field]: {
            id: "cnt_saved",
            size_bytes: 50000,
            media_type: kind === "tool" ? "application/json" : "text/plain",
            preview:
              kind === "tool" ? '{"answer":"Saved preview' : "Saved preview",
          },
        },
      },
    ];
    const contentRequests: Request[] = [];
    client = createClient({
      baseUrl: "https://test.invalid",
      auth: { type: "session" },
      fetch: async (input) => {
        const request = input as Request;
        const url = new URL(request.url);
        if (url.pathname.includes("/contents/")) {
          contentRequests.push(request);
          return Response.json({
            id: "cnt_saved",
            media_type: displayItems[0]!.content_refs![field]!.media_type,
            value,
          });
        }
        if (url.pathname.endsWith("/lineage"))
          return Response.json({
            items: ["current", "parent"].map(ancestor),
            next_cursor: null,
          });
        if (url.pathname.endsWith("/runs") || url.pathname.endsWith("/threads"))
          return Response.json({ items: [], next_cursor: null });
        if (url.pathname.endsWith("/items"))
          return Response.json({
            run: ancestor("parent"),
            baseline: true,
            complete: true,
            position: "2-1",
            items: displayItems,
          });
        if (url.pathname.endsWith("/attempts"))
          return Response.json({ items: [] });
        if (url.pathname.endsWith("/usage"))
          return Response.json({ models: [], runs: [] });
        return Response.json({ ...ancestor("parent"), lineage: "root" });
      },
    });
    const { cache } = stage("debug");
    fireEvent.click(
      await screen.findByRole("button", { name: "Load earlier runs" }),
    );
    await screen.findByText("Run");
    // Debug reads each entry as a step: its content opens with its row.
    fireEvent.click(
      await screen.findByRole("button", {
        name:
          kind === "tool"
            ? /read_file/
            : kind === "reply"
              ? /Reply/
              : /Reasoning/,
      }),
    );
    expect(contentRequests).toHaveLength(0);
    fireEvent.click(
      await screen.findByRole("button", { name: "Expand full content" }),
    );
    expect(
      await screen.findByText(
        kind === "tool" ? /Full saved result/ : "Full saved message",
      ),
    ).toBeTruthy();
    expect(contentRequests).toHaveLength(1);
    expect(new URL(contentRequests[0]!.url).pathname).toBe(
      "/api/v1/runs/parent/contents/cnt_saved",
    );
    expect(contentRequests[0]!.headers.get("x-workspace-id")).toBe("workspace");
    cache.clear();
  },
);

it("loads one Debug ancestor per click and preserves that range across repeated Chat switches", async () => {
  const requests: URL[] = [];
  client = ancestorService(requests, [["current", "parent", "grandparent"]]);
  const view = stage("debug");
  const earlier = await screen.findByRole("button", {
    name: "Load earlier runs",
  });
  await waitFor(() => expect(earlier).not.toHaveProperty("disabled", true));
  const itemReads = () =>
    requests.filter((url) => url.pathname.endsWith("/items"));
  expect(itemReads()).toHaveLength(0);
  fireEvent.click(earlier);
  fireEvent.click(earlier);
  await screen.findByText("Latest parent message");
  expect(itemReads().map((url) => url.pathname)).toEqual([
    "/api/v1/runs/parent/items",
  ]);
  expect(screen.queryByText("Latest grandparent message")).toBeNull();
  view.switchLevel("chat");
  expect(screen.getByText("Latest parent message")).toBeTruthy();
  await waitFor(() =>
    expect(StageObserver.created.at(-1)?.targets.length).toBe(1),
  );
  act(() => StageObserver.created.at(-1)!.intersect());
  expect(itemReads()).toHaveLength(1);
  view.switchLevel("debug");
  expect(screen.getByText("Latest parent message")).toBeTruthy();
  view.switchLevel("chat");
  expect(screen.getByText("Latest parent message")).toBeTruthy();
  expect(itemReads()).toHaveLength(1);
  await readEarlier();
  await screen.findByText("Latest grandparent message");
  expect(itemReads()).toHaveLength(2);
  expect(view.viewport.querySelector('[data-slot="skeleton"]')).toBeNull();
  view.cache.clear();
});

it("prepares all of an earlier Debug Run before insertion and preserves position during a slow page", async () => {
  let finish!: () => void;
  const waiting = new Promise<void>((resolve) => {
    finish = resolve;
  });
  const requests: URL[] = [];
  const item = (ordinal: number) => ({
    id: `itm_${ordinal}`,
    ordinal,
    kind: "text_message",
    state: "completed",
    first_stream_id: `1-${ordinal}`,
    last_stream_id: `1-${ordinal}`,
    content: { role: "assistant", text: `Saved reply ${ordinal}` },
  });
  client = createClient({
    baseUrl: "https://test.invalid",
    auth: { type: "session" },
    fetch: async (input) => {
      const url = new URL((input as Request).url);
      requests.push(url);
      if (url.pathname.endsWith("/lineage"))
        return Response.json({
          items: ["current", "parent", "grandparent"].map(ancestor),
          next_cursor: null,
        });
      if (url.pathname.endsWith("/items")) {
        const historical = url.searchParams.has("after");
        if (historical) await waiting;
        return Response.json({
          run: ancestor("parent"),
          baseline: !historical,
          position: historical ? null : "1-3",
          complete: true,
          items: historical ? [item(1), item(2)] : [item(3)],
        });
      }
      if (url.pathname.endsWith("/usage"))
        return Response.json({ models: [], runs: [] });
      if (
        ["/attempts", "/runs", "/threads"].some((path) =>
          url.pathname.endsWith(path),
        )
      )
        return Response.json({ items: [], next_cursor: null });
      return Response.json(ancestor("parent"));
    },
  });
  const { viewport, cache } = stage("debug");
  const button = await screen.findByRole("button", {
    name: "Load earlier runs",
  });
  await waitFor(() => expect(button).not.toHaveProperty("disabled", true));
  fireEvent.wheel(viewport, { deltaY: -200 });
  viewport.scrollTop = 100;
  fireEvent.scroll(viewport);
  fireEvent.click(button);
  await waitFor(() =>
    expect(requests.some((url) => url.searchParams.has("after"))).toBe(true),
  );
  expect(viewport.querySelectorAll("[data-run]")).toHaveLength(1);
  expect(viewport.querySelector('[data-slot="skeleton"]')).toBeNull();
  expect(screen.queryByText(/Execution history is incomplete/)).toBeNull();
  viewport.scrollTop = 150;
  fireEvent.scroll(viewport);
  const anchor = viewport.querySelector('[data-run="current"]')!;
  const top = anchor.getBoundingClientRect().top;
  await act(async () => finish());
  await screen.findByText("Saved reply 1");
  expect(screen.getByText("Saved reply 3")).toBeTruthy();
  expect(anchor.getBoundingClientRect().top).toBe(top);
  expect(requests.some((url) => url.pathname.includes("/grandparent"))).toBe(
    false,
  );
  expect(
    requests.filter((url) => url.pathname.endsWith("/items")),
  ).toHaveLength(2);
  expect(screen.queryByText(/Execution history is incomplete/)).toBeNull();
  cache.clear();
});

it("leaves loaded Runs in place when an earlier Run fails and retries without automatic request loops", async () => {
  let fail = true;
  const requests: URL[] = [];
  client = ancestorService(requests, [["current", "parent"]], async (url) => {
    if (url.pathname.endsWith("/parent/items") && fail)
      throw new Error("History unavailable");
  });
  const { viewport, cache } = stage("debug");
  const button = await screen.findByRole("button", {
    name: "Load earlier runs",
  });
  await waitFor(() => expect(button).not.toHaveProperty("disabled", true));
  fireEvent.click(button);
  await screen.findByRole("alert");
  expect(viewport.querySelectorAll("[data-run]")).toHaveLength(1);
  expect(viewport.querySelector('[data-slot="skeleton"]')).toBeNull();
  expect(
    requests.filter((url) => url.pathname.endsWith("/items")),
  ).toHaveLength(1);
  expect(button).toHaveProperty("disabled", true);
  fail = false;
  fireEvent.click(screen.getByRole("button", { name: "Reload" }));
  await screen.findByText("Latest parent message");
  expect(
    requests.filter((url) => url.pathname.endsWith("/items")),
  ).toHaveLength(2);
  expect(screen.queryByRole("alert")).toBeNull();
  cache.clear();
});

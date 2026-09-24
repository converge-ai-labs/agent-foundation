// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { createClient, type Client } from "../../../service-client";
import { fixtureRun, fixtureThread } from "./fixture";
import { HistoryTranscript } from "./history";

let client: Client;
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
vi.mock("../run-display", () => ({
  useRunDisplay: () => ({
    items: [],
    state: "connected",
    execution: {
      steps: [],
      observations: [],
      retries: [],
      usage: { model: [], provider: [], recordIds: [] },
      contextTokens: {},
      coverage: "complete",
    },
  }),
}));
vi.mock("./user-message", () => ({
  RequestContent: () => null,
  UserMessage: () => null,
  requestLabel: () => "You",
}));
vi.mock("./assistant-message", () => ({
  AgentTurn: ({
    blocks,
  }: {
    blocks: { id: string; entry?: { text: string } }[];
  }) => (
    <>
      {blocks.map((block) => (
        <p key={block.id} data-message-id={block.id}>
          {block.entry?.text}
        </p>
      ))}
    </>
  ),
}));

/** The reader's viewport, which the sentinel and the ancestors live in. */
class StageObserver {
  static created: StageObserver[] = [];
  targets: Element[] = [];
  constructor(private callback: IntersectionObserverCallback) {
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
  StageObserver.created = [];
  vi.stubGlobal("IntersectionObserver", StageObserver);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  client?.close();
});

function stage(level: "chat" | "debug") {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const view = render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <div data-session-stage>
          <HistoryTranscript runId="current" thread={thread} level={level} />
          <div data-run="current">Current run</div>
        </div>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return {
    cache,
    viewport: view.container.querySelector<HTMLElement>(
      "[data-session-stage]",
    )!,
  };
}

/** A Run of the fixture Thread, as the lineage and the Run read serve it. */
const ancestor = (id: string) =>
  fixtureRun({
    id,
    thread_id: "thread",
    session_id: "session",
    agent_id: "agent",
    input: { content: [{ type: "text", text: "Earlier request" }] },
  });

/** The lineage in pages, nearest first, as the Service pages it. */
function ancestorService(
  requests: URL[],
  pages: string[][] = [["current", "parent", "grandparent"]],
) {
  return createClient({
    baseUrl: "https://test.invalid",
    auth: { type: "session" },
    fetch: async (input) => {
      const url = new URL((input as Request).url);
      requests.push(url);
      if (url.pathname.endsWith("/lineage")) {
        const index = Number(url.searchParams.get("cursor") ?? 0);
        return Response.json({
          items: pages[index]!.map(ancestor),
          next_cursor: index + 1 < pages.length ? String(index + 1) : null,
        });
      }
      if (url.pathname.endsWith("/threads/thread/runs"))
        return Response.json({ items: [], next_cursor: null });
      if (url.pathname.endsWith("/items")) {
        const run = url.pathname.split("/").at(-2)!;
        return Response.json({
          run: ancestor(run),
          position: "2-0",
          complete: true,
          items: [
            {
              id: run,
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

it("reaches one run further back each time the reader scrolls to the top", async () => {
  const requests: URL[] = [];
  client = ancestorService(requests);
  const { cache, viewport } = stage("chat");
  // Nothing but the lineage until the sentinel above the transcript is seen.
  await waitFor(() => expect(StageObserver.created).toHaveLength(1));
  expect(requests.map((url) => url.pathname)).toEqual([
    "/api/v1/workspaces/workspace/runs/current/lineage",
  ]);
  expect(
    screen.queryByRole("button", { name: "Load earlier runs" }),
  ).toBeNull();
  StageObserver.created[0]!.reach();
  // While the run loads the stage is marked, so the transcript holds still.
  expect(viewport.dataset.loadingEarlier).toBe("true");
  // One run at a time: the next one waits for this one to render.
  expect(StageObserver.created).toHaveLength(1);
  expect(requests.some((url) => url.pathname.includes("grandparent"))).toBe(
    false,
  );
  await screen.findByText("Latest parent message");
  await waitFor(() => expect(StageObserver.created).toHaveLength(2));
  expect(viewport.dataset.loadingEarlier).toBeUndefined();
  StageObserver.created[1]!.reach();
  await screen.findByText("Latest grandparent message");
  cache.clear();
});

it("reads the next lineage page only once the reader reaches past the loaded one", async () => {
  const requests: URL[] = [];
  client = ancestorService(requests, [["current", "parent"], ["grandparent"]]);
  const { cache } = stage("chat");
  const lineage = () =>
    requests.filter((url) => url.pathname.endsWith("/lineage"));
  await waitFor(() => expect(StageObserver.created).toHaveLength(1));
  StageObserver.created[0]!.reach();
  await screen.findByText("Latest parent message");
  // The loaded page still had the parent: no further page was read for it.
  expect(lineage()).toHaveLength(1);
  await waitFor(() => expect(StageObserver.created).toHaveLength(2));
  StageObserver.created[1]!.reach();
  await screen.findByText("Latest grandparent message");
  expect(lineage().map((url) => url.searchParams.get("cursor"))).toEqual([
    null,
    "1",
  ]);
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
      return Response.json({ ...ancestor("parent"), lineage: "root" });
    },
  });
  const { cache } = stage("debug");
  fireEvent.click(
    await screen.findByRole("button", { name: "Load earlier runs" }),
  );
  expect(await screen.findByText("Run")).toBeTruthy();
  expect(screen.getByText("state.completed")).toBeTruthy();
  // The section reads the ancestor's display itself; the list never fetches it.
  expect(requests.some((url) => url.pathname.endsWith("/items"))).toBe(false);
  cache.clear();
});

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
import { fixtureThread } from "./fixture";
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
vi.mock("../run-stream", () => ({
  useRunStream: () => ({
    items: [],
    state: "connected",
    execution: {
      steps: [],
      observations: [],
      events: [],
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

function ancestorService(requests: URL[]) {
  return createClient({
    baseUrl: "https://test.invalid",
    auth: { type: "session" },
    fetch: async (input) => {
      const url = new URL((input as Request).url);
      requests.push(url);
      if (url.pathname.endsWith("/lineage"))
        return Response.json({
          items: [
            { run_id: "current", thread_id: "thread", depth_from_head: 0 },
            { run_id: "parent", thread_id: "thread", depth_from_head: 1 },
            {
              run_id: "grandparent",
              thread_id: "thread",
              depth_from_head: 2,
            },
          ],
        });
      if (url.pathname.endsWith("/threads/thread/runs"))
        return Response.json({ items: [], next_cursor: null });
      if (url.pathname.endsWith("/items")) {
        const older = url.searchParams.has("cursor");
        const run = url.pathname.split("/").at(-2);
        return Response.json({
          snapshot_version: 1,
          projection_cursor: "3-0",
          complete: true,
          finalized: true,
          incomplete_reason: null,
          next_cursor: older ? null : "older",
          items: [
            {
              id: older ? "old" : run,
              kind: "text_message",
              state: "completed",
              parent_item_id: null,
              first_stream_id: older ? "1-0" : "2-0",
              last_stream_id: older ? "1-0" : "2-0",
              content: {
                text: older
                  ? `Earlier ${run} message`
                  : `Latest ${run} message`,
              },
            },
          ],
        });
      }
      return Response.json({
        id: url.pathname.split("/").at(-1),
        thread_id: "thread",
        session_id: "session",
        agent_id: "agent",
        status: "completed",
        created_at: "2026-09-17T00:00:00Z",
      });
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
    "/api/v1/runs/current/lineage",
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
  // An ancestor's own earlier items still load on demand, inside that run.
  fireEvent.click(
    screen.getAllByRole("button", { name: "Load earlier messages" })[0]!,
  );
  await screen.findByText("Earlier grandparent message");
  expect(requests.at(-1)?.searchParams.get("cursor")).toBe("older");
  cache.clear();
});

it("reveals an ancestor as a debug section, replayed on its own", async () => {
  const requests: URL[] = [];
  client = createClient({
    baseUrl: "https://test.invalid",
    auth: { type: "session" },
    fetch: async (input) => {
      const url = new URL((input as Request).url);
      requests.push(url);
      if (url.pathname.endsWith("/lineage"))
        return Response.json({
          items: [
            { run_id: "current", thread_id: "thread", depth_from_head: 0 },
            { run_id: "parent", thread_id: "thread", depth_from_head: 1 },
          ],
        });
      if (url.pathname.endsWith("/runs"))
        return Response.json({ items: [], next_cursor: null });
      if (url.pathname.endsWith("/threads"))
        return Response.json({ items: [], next_cursor: null });
      return Response.json({
        id: "parent",
        thread_id: "thread",
        session_id: "session",
        agent_id: "agent",
        status: "completed",
        input_kind: "agent_input",
        input_text: "Earlier request",
        lineage_kind: "root",
        created_at: "2026-09-17T00:00:00Z",
        started_at: "2026-09-17T00:00:00Z",
        completed_at: "2026-09-17T00:00:04Z",
      });
    },
  });
  const { cache } = stage("debug");
  fireEvent.click(
    await screen.findByRole("button", { name: "Load earlier runs" }),
  );
  expect(await screen.findByText("Run")).toBeTruthy();
  expect(screen.getByText("state.completed")).toBeTruthy();
  // The ancestor's own Items are never fetched: its stream replays them.
  expect(requests.some((url) => url.pathname.endsWith("/items"))).toBe(false);
  cache.clear();
});

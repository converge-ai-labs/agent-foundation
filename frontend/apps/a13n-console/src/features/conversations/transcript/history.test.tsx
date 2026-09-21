// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
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
afterEach(() => {
  cleanup();
  client?.close();
});

it("defers ancestor details and loads their earlier Items only on demand", async () => {
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
            { run_id: "current", depth_from_head: 0 },
            { run_id: "parent", depth_from_head: 1 },
            { run_id: "grandparent", depth_from_head: 2 },
          ],
        });
      if (url.pathname.endsWith("/items")) {
        const older = url.searchParams.has("cursor");
        return Response.json({
          snapshot_version: 1,
          projection_cursor: "3-0",
          complete: true,
          finalized: true,
          incomplete_reason: null,
          next_cursor: older ? null : "older",
          items: [
            {
              id: older ? "old" : "new",
              kind: "text_message",
              state: "completed",
              parent_item_id: null,
              first_stream_id: older ? "1-0" : "2-0",
              last_stream_id: older ? "1-0" : "2-0",
              content: {
                text: older
                  ? "Earlier parent message"
                  : "Latest parent message",
              },
            },
          ],
        });
      }
      return Response.json({
        id: "parent",
        thread_id: "thread",
        session_id: "session",
        agent_id: "agent",
        status: "completed",
        created_at: "2026-09-17T00:00:00Z",
      });
    },
  });
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <HistoryTranscript runId="current" thread={thread} level="chat" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByRole("button", { name: "Load earlier runs" });
  expect(requests.map((url) => url.pathname)).toEqual([
    "/api/v1/runs/current/lineage",
  ]);
  fireEvent.click(screen.getByRole("button", { name: "Load earlier runs" }));
  await screen.findByText("Latest parent message");
  expect(
    requests.filter((url) => url.pathname.endsWith("/items")),
  ).toHaveLength(1);
  expect(requests.some((url) => url.pathname.includes("grandparent"))).toBe(
    false,
  );
  // The first control loads an ancestor Run; the second loads that Run's earlier Items.
  fireEvent.click(
    screen.getByRole("button", { name: "Load earlier messages" }),
  );
  await screen.findByText("Earlier parent message");
  await waitFor(() =>
    expect(
      requests.filter((url) => url.pathname.endsWith("/items")),
    ).toHaveLength(2),
  );
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
            { run_id: "current", depth_from_head: 0 },
            { run_id: "parent", depth_from_head: 1 },
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
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <HistoryTranscript runId="current" thread={thread} level="debug" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  fireEvent.click(
    await screen.findByRole("button", { name: "Load earlier runs" }),
  );
  expect(await screen.findByText("Run")).toBeTruthy();
  expect(screen.getByText("state.completed")).toBeTruthy();
  // The ancestor's own Items are never fetched: its stream replays them.
  expect(requests.some((url) => url.pathname.endsWith("/items"))).toBe(false);
  cache.clear();
});

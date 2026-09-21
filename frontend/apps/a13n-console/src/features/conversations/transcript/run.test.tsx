// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { createClient, type Client } from "../../../service-client";
import { RunContent } from "./run";

let client: Client;
let cache: QueryClient;
const requests: Request[] = [];
vi.mock("../../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "workspace" },
    basePath: "/workspace/design",
    can: () => true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) =>
      options
        ? key.replace(/{{(\w+)}}/g, (_, name) => String(options[name] ?? ""))
        : key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
const stream = vi.hoisted(() => ({ calls: [] as { replay?: boolean }[] }));
vi.mock("../run-stream", () => ({
  useRunStream: (_runId: string, options: { replay?: boolean } = {}) => {
    stream.calls.push(options);
    return {
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
    };
  },
}));
vi.mock("./history", () => ({ HistoryTranscript: () => null }));
vi.mock("../agents/queries", () => ({ useAgent: () => ({ data: null }) }));
vi.mock("./queue", () => ({ ThreadQueue: () => <p>Thread queue</p> }));
const receipt = {
  run_id: "run_retry",
  thread_id: "thread",
  session_id: "session",
};
beforeEach(() => {
  requests.length = 0;
  stream.calls.length = 0;
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session", csrfToken: "csrf" },
    fetch: async (input, init) => {
      const request = new Request(input, init);
      requests.push(request);
      const path = new URL(request.url).pathname;
      if (path.endsWith("/retry")) return Response.json(receipt);
      if (path.endsWith("/pending-actions"))
        return Response.json({ items: [] });
      if (path.endsWith("/threads/thread"))
        return Response.json({
          id: "thread",
          session_id: "session",
          version: 7,
          current_run_id: "run",
          head_run_id: "run",
        });
      if (path.endsWith("/threads/thread/runs"))
        return Response.json({ items: [], next_cursor: null });
      if (path.endsWith("/sessions/session/threads"))
        return Response.json({ items: [], next_cursor: null });
      if (path.endsWith("/runs/run"))
        return Response.json({
          id: "run",
          version: 3,
          thread_id: "thread",
          session_id: "session",
          agent_id: "agent",
          status: "failed",
          input_kind: "agent_input",
          input: "Build an agent",
          input_text: "Build an agent",
          created_at: "2026-09-16T00:00:00Z",
        });
      throw new Error(`Unexpected request: ${path}`);
    },
  });
});
afterEach(() => {
  cleanup();
  cache.clear();
  client.close();
  vi.unstubAllGlobals();
});
function show(
  configuration?: Parameters<typeof RunContent>[0]["configuration"],
  entry = "/",
) {
  return render(
    <QueryClientProvider client={cache}>
      <MemoryRouter initialEntries={[entry]}>
        <RunContent
          runId="run"
          threadId="thread"
          sessionId="session"
          configuration={configuration}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
it("keeps ordinary run observation free of configuration input and retry controls", async () => {
  show();
  expect(await screen.findByText("Build an agent")).toBeTruthy();
  expect(screen.getByText("Thread queue")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Retry run" })).toBeNull();
  expect(screen.queryByRole("textbox")).toBeNull();
});
it("retains assistant composition and retries through the protected conversation callback", async () => {
  const accepted = vi.fn();
  show({ composer: <textarea aria-label="Assistant input" />, accepted });
  expect(
    await screen.findByRole("textbox", { name: "Assistant input" }),
  ).toBeTruthy();
  expect(screen.queryByText("Thread queue")).toBeNull();
  await userEvent
    .setup()
    .click(screen.getByRole("button", { name: "Retry run" }));
  await waitFor(() => expect(accepted).toHaveBeenCalledWith(receipt));
  const request = requests.find((item) => item.method === "POST")!;
  expect(new URL(request.url).pathname).toBe("/api/v1/runs/run/retry");
  expect(await request.json()).toEqual({
    expected_thread_version: 7,
  });
});
it("reads the level from the URL: chat keeps the snapshot, debug replays", async () => {
  show();
  expect(await screen.findByText("Build an agent")).toBeTruthy();
  expect(stream.calls.at(-1)).toEqual({ replay: false });
  cleanup();
  show(undefined, "/?view=debug");
  // Debug reads one run as a section, with its terminal fact in the timeline.
  expect(await screen.findByText("Run")).toBeTruthy();
  expect(stream.calls.at(-1)).toEqual({ replay: true });
  expect(screen.getByText("Run failed")).toBeTruthy();
  // The details card stays lazy; only the navigator's lineage read happens.
  const panels = requests.filter((request) =>
    ["attempts", "events"].includes(
      new URL(request.url).pathname.split("/").at(-1) ?? "",
    ),
  );
  expect(panels).toEqual([]);
});

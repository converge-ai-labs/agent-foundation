import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createClient, type Client } from "../../service-client";
import type { Schema } from "../../shared/api";
import { ToolExecutions, ThreadToolExecutionsDialog } from "./tool-executions";

let client: Client;
let cache: QueryClient;
let calls: URL[];
let status: number;
let resultStatus: number;
let active: boolean;
let resultText: string;
vi.mock("../../auth/context", () => ({
  useClient: () => client,
  revalidateSession: vi.fn(),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_1" } }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) =>
      key.replace(/{{(\w+)}}/g, (_, name) => String(options?.[name] ?? "")),
    i18n: { resolvedLanguage: "en" },
  }),
}));

function execution(id = "exec_1"): Schema["ExecutionView"] {
  return {
    execution_id: id,
    thread_id: "thr_1",
    run_id: "run_1",
    attempt_id: "att_1",
    worker_id: "wrk_1",
    tool_call_id: "call_1",
    tool_name: id === "exec_1" ? "shell_exec" : "run_code",
    status: active ? "running" : "succeeded",
    created_at: "2026-10-21T09:00:00Z",
    completed_at: active ? null : "2026-10-21T09:01:00Z",
    expires_at: "2026-10-22T09:01:00Z",
    cancel_requested_at: null,
    first_delivered_at: null,
    result_available: !active,
  };
}

beforeEach(() => {
  calls = [];
  status = 200;
  resultStatus = 200;
  active = false;
  resultText = "first fragment";
  cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
    fetch: async (input, init) => {
      const request = new Request(input, init),
        url = new URL(request.url);
      expect(request.headers.get("X-Workspace-Id")).toBe("ws_1");
      calls.push(url);
      expect(request.method).toBe("GET");
      if (url.pathname.endsWith("/observation"))
        return Response.json({
          execution: execution(),
          source: active ? "live" : "retained",
          shell: {
            status: {
              phase: active ? "running" : "exited",
              cleanup: "complete",
              exit_code: active ? null : 0,
            },
            stdout: {
              text: active ? "live progress" : "finished output",
              truncated: true,
              produced_bytes: 123,
              observation_closed: !active,
            },
            stderr: {
              text: "",
              truncated: false,
              produced_bytes: 0,
              observation_closed: !active,
            },
          },
        });
      if (url.pathname.endsWith("/result")) {
        if (resultStatus !== 200)
          return Response.json(
            { error: { code: "not_found", message: "Result expired" } },
            { status: resultStatus },
          );
        const offset = Number(url.searchParams.get("offset"));
        return Response.json({
          representation: "fragment",
          media_type: "application/json",
          text: offset ? "second fragment" : resultText,
          offset,
          total_chars: 28,
          has_more: !offset,
          next_offset: offset ? null : 14,
          version: "v1",
          source: "tool-executions/exec_1/result",
        });
      }
      if (status !== 200)
        return Response.json(
          {
            error: { code: "unavailable", message: "Temporarily unavailable" },
          },
          { status },
        );
      return Response.json({
        items: url.pathname.includes("thr_empty")
          ? []
          : [execution(url.searchParams.has("cursor") ? "exec_2" : "exec_1")],
        next_cursor: url.searchParams.has("cursor") ? null : "next",
      });
    },
  });
});
afterEach(() => {
  cleanup();
  cache.clear();
  client.close();
});
const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={cache}>{children}</QueryClientProvider>
);

it("loads only an open inspector, reads bounded versioned fragments on demand, and clears them on close", async () => {
  const user = userEvent.setup();
  const view = render(
    <ThreadToolExecutionsDialog
      threadId="thr_1"
      open={false}
      onOpenChange={() => {}}
    />,
    { wrapper },
  );
  expect(calls).toHaveLength(0);
  view.rerender(
    <ThreadToolExecutionsDialog
      threadId="thr_1"
      open
      onOpenChange={() => {}}
    />,
  );
  await user.click(
    await screen.findByRole("button", { name: "Inspect shell_exec" }),
  );
  expect(calls.some((url) => url.pathname.endsWith("/result"))).toBe(false);
  await user.click(screen.getByRole("button", { name: "Read result" }));
  expect(await screen.findByText("first fragment")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Next fragment" }));
  expect(await screen.findByText("second fragment")).toBeTruthy();
  expect(screen.queryByText("first fragment")).not.toBeTruthy();
  expect(calls.at(-1)?.searchParams.get("version")).toBe("v1");
  expect(calls.at(-1)?.searchParams.get("offset")).toBe("14");
  expect(calls.at(-1)?.searchParams.get("limit")).toBe("4000");
  expect(
    JSON.stringify(
      cache
        .getQueryCache()
        .getAll()
        .map((q) => q.state.data),
    ),
  ).not.toContain("fragment");
  await user.click(screen.getByRole("button", { name: "Inspect shell_exec" }));
  await user.click(screen.getByRole("button", { name: "Inspect shell_exec" }));
  expect(screen.getByRole("button", { name: "Read result" })).toBeTruthy();
});

it("counts supplementary Unicode characters using the server's code-point offsets", async () => {
  resultText = "A\u{1D11E}B";
  const user = userEvent.setup();
  render(<ToolExecutions threadId="thr_1" />, { wrapper });
  await user.click(
    await screen.findByRole("button", { name: "Inspect shell_exec" }),
  );
  await user.click(screen.getByRole("button", { name: "Read result" }));
  expect(await screen.findByText("Characters 1–3 of 28")).toBeTruthy();
});

it("preserves Run scope through pagination and refreshes completion independently of Run state", async () => {
  active = true;
  const user = userEvent.setup();
  render(<ToolExecutions threadId="thr_1" runId="run_1" />, { wrapper });
  await user.click(
    await screen.findByRole("button", { name: "Inspect shell_exec" }),
  );
  expect(screen.getByText("The result is not available yet.")).toBeTruthy();
  active = false;
  await user.click(screen.getByRole("button", { name: "Refresh" }));
  expect(
    await screen.findByRole("button", { name: "Read result" }),
  ).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Load more" }));
  expect(await screen.findByText("run_code")).toBeTruthy();
  expect(
    calls
      .filter((url) => url.pathname.endsWith("/tool-executions"))
      .every((url) => url.searchParams.get("run_id") === "run_1"),
  ).toBe(true);
  expect(calls.at(-1)?.searchParams.get("cursor")).toBe("next");
});

it("shows recoverable list and expired-result errors and isolates a changed Thread", async () => {
  status = 503;
  const user = userEvent.setup();
  const view = render(<ToolExecutions threadId="thr_1" />, { wrapper });
  expect(await screen.findByText("Temporarily unavailable")).toBeTruthy();
  status = 200;
  await user.click(screen.getByRole("button", { name: "Reload" }));
  await user.click(
    await screen.findByRole("button", { name: "Inspect shell_exec" }),
  );
  resultStatus = 404;
  await user.click(screen.getByRole("button", { name: "Read result" }));
  expect(await screen.findByText("Result expired")).toBeTruthy();
  resultStatus = 200;
  await user.click(screen.getByRole("button", { name: "Reload" }));
  expect(await screen.findByText("first fragment")).toBeTruthy();
  view.rerender(<ToolExecutions threadId="thr_empty" />);
  await waitFor(() =>
    expect(screen.queryByText("shell_exec")).not.toBeTruthy(),
  );
  expect(await screen.findByText("No retained tool executions.")).toBeTruthy();
});

it("shows bounded live and terminal output using only reads and keeps payloads out of the query cache", async () => {
  active = true;
  const user = userEvent.setup();
  render(<ToolExecutions threadId="thr_1" />, { wrapper });
  await user.click(
    await screen.findByRole("button", { name: "Inspect shell_exec" }),
  );
  expect(await screen.findByText("live progress")).toBeTruthy();
  expect(
    screen.getByText(
      "Partial output preview. Some captured content is omitted.",
    ),
  ).toBeTruthy();
  expect(
    screen.queryByRole("button", { name: /cancel|signal|stdin/i }),
  ).toBeNull();
  active = false;
  await user.click(screen.getByRole("button", { name: "Refresh output" }));
  expect(await screen.findByText("finished output")).toBeTruthy();
  expect(screen.queryByText("live progress")).toBeNull();
  expect(
    JSON.stringify(
      cache
        .getQueryCache()
        .getAll()
        .map((query) => query.state.data),
    ),
  ).not.toContain("finished output");
  await user.click(screen.getByRole("button", { name: "Inspect shell_exec" }));
  expect(screen.queryByText("finished output")).toBeNull();
});

// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { createClient, type Client } from "@converge.ai/a13n";
import { ConversationsPage } from "./page";

let client: Client;
vi.mock("../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    basePath: "/workspace/design",
    workspace: { id: "workspace" },
    can: () => false,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
afterEach(() => {
  cleanup();
  client.close();
});

it("renders a full Session page from collection previews without per-row Thread or Run requests", async () => {
  const requests: string[] = [];
  const urls: URL[] = [];
  const user = userEvent.setup();
  const copy = vi.spyOn(navigator.clipboard, "writeText");
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
    fetch: async (input, init) => {
      const request = new Request(input, init);
      requests.push(new URL(request.url).pathname);
      urls.push(new URL(request.url));
      return Response.json({
        items: Array.from({ length: 20 }, (_, index) => ({
          id: `session_${index}`,
          workspace_id: "workspace",
          created_at: "2026-09-09T00:00:00Z",
          updated_at: "2026-09-09T00:00:00Z",
          run_count: index === 19 ? null : index + 1,
          preview:
            index === 19
              ? null
              : {
                  thread_id: `thread_${index}`,
                  run_id: `run_${index}`,
                  input_text: `Question ${index}`,
                  output_text: `**Answer ${index}**`,
                  agent_name: `Agent ${index}`,
                  run_status: "completed",
                  trigger_type: "user_input",
                },
        })),
        next_cursor: null,
      });
    },
  });
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter initialEntries={["/workspace/design/sessions"]}>
        <Routes>
          <Route
            path="/workspace/:workspaceKey/sessions"
            element={<ConversationsPage />}
          >
            <Route path=":sessionId/*" element={<SelectedDetail />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(await screen.findByText("Question 18")).toBeTruthy();
  expect(screen.getByText("Agent 18")).toBeTruthy();
  expect(screen.getByText("session_18")).toBeTruthy();
  expect(screen.getByText("19")).toBeTruthy();
  expect(
    screen.getAllByRole("columnheader").map((column) => column.textContent),
  ).toEqual([
    "Session ID",
    "Request summary",
    "Recent agent",
    "Recent run status",
    "Runs",
    "Trigger source",
    "Last updated",
  ]);
  expect(screen.getByText("No request text")).toBeTruthy();
  expect(screen.getAllByText("—")).toHaveLength(4);
  expect(screen.getByRole("searchbox")).toBeTruthy();
  expect(screen.queryByText("Session detail")).toBeNull();
  expect(requests).toEqual(["/api/v1/workspaces/workspace/sessions"]);
  const row = screen.getByText("Question 0").closest("tr")!;
  await user.click(within(row).getByRole("button", { name: "Copy ID" }));
  expect(copy).toHaveBeenCalledWith("session_0");
  expect(screen.queryByText("Session detail")).toBeNull();
  await user.click(within(row).getByText("Agent 0"));
  expect(
    await screen.findByText("/workspace/design/sessions/session_0"),
  ).toBeTruthy();
  cleanup();
  cache.clear();
});

function SelectedDetail() {
  return <p>{useLocation().pathname}</p>;
}

it("submits ID search, combines filters, and opens the matched Thread", async () => {
  const urls: URL[] = [];
  const user = userEvent.setup();
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
    fetch: async (input, init) => {
      const url = new URL(new Request(input, init).url);
      urls.push(url);
      return Response.json({
        items: [
          {
            id: "sess_one",
            workspace_id: "workspace",
            created_at: "2026-09-09T00:00:00Z",
            updated_at: "2026-09-09T00:00:00Z",
            preview: null,
            run_count: 0,
          },
        ],
        next_cursor: null,
      });
    },
  });
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter
        initialEntries={["/workspace/design/sessions?agent_id=agt_one"]}
      >
        <Routes>
          <Route
            path="/workspace/:workspaceKey/sessions"
            element={<ConversationsPage />}
          >
            <Route path=":sessionId/*" element={<SelectedDetail />} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByText("sess_one");
  expect(urls[0].searchParams.get("agent_id")).toBe("agt_one");
  await user.type(screen.getByRole("searchbox"), "  thread_one  ");
  expect(urls).toHaveLength(1);
  await user.keyboard("{Enter}");
  await waitFor(() =>
    expect(urls.at(-1)?.searchParams.get("q")).toBe("thread_one"),
  );
  await user.click(screen.getByRole("button", { name: "Run status" }));
  await user.click(
    await screen.findByRole("menuitemcheckbox", { name: "state.failed" }),
  );
  await user.click(
    await screen.findByRole("menuitemcheckbox", { name: "state.waiting" }),
  );
  await user.keyboard("{Escape}");
  await waitFor(() =>
    expect(urls.at(-1)?.searchParams.getAll("status")).toEqual([
      "failed",
      "waiting",
    ]),
  );
  await user.click(screen.getByRole("button", { name: "Trigger source" }));
  await user.click(
    await screen.findByRole("menuitemcheckbox", { name: "trigger.inbound" }),
  );
  await user.keyboard("{Escape}");
  await waitFor(() =>
    expect(urls.at(-1)?.searchParams.getAll("trigger_type")).toEqual([
      "inbound",
    ]),
  );
  await user.click(screen.getByRole("button", { name: "Last updated" }));
  expect(
    await screen.findByRole("button", { name: "Start time" }),
  ).toBeTruthy();
  expect(screen.getByRole("button", { name: "End time" })).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Last 7 days" }));
  expect(urls.at(-1)?.searchParams.has("updated_after")).toBe(false);
  await user.click(screen.getByRole("button", { name: "Apply" }));
  await waitFor(() =>
    expect(urls.at(-1)?.searchParams.has("updated_before")).toBe(true),
  );
  const bounds = urls.at(-1)!.searchParams;
  expect(
    Date.parse(bounds.get("updated_before")!) -
      Date.parse(bounds.get("updated_after")!),
  ).toBe(7 * 86400000);
  expect(urls.at(-1)?.searchParams.get("agent_id")).toBe("agt_one");
  await user.click(screen.getByText("No request text").closest("tr")!);
  expect(
    await screen.findByText(
      "/workspace/design/sessions/sess_one/threads/thread_one",
    ),
  ).toBeTruthy();
  cleanup();
  cache.clear();
});

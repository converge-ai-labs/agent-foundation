// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { createClient, type Client } from "@converge.ai/a13n";
import { ConversationsPage } from "./page";

let client: Client;
vi.mock("../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "workspace" }, can: () => false }),
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
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
    fetch: async (input, init) => {
      const request = new Request(input, init);
      requests.push(new URL(request.url).pathname);
      return Response.json({
        items: Array.from({ length: 20 }, (_, index) => ({
          id: `session_${index}`,
          workspace_id: "workspace",
          created_at: "2026-09-09T00:00:00Z",
          updated_at: "2026-09-09T00:00:00Z",
          preview:
            index === 19
              ? null
              : {
                  thread_id: `thread_${index}`,
                  run_id: `run_${index}`,
                  input_text: `Question ${index}`,
                  output_text: `**Answer ${index}**`,
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
      <MemoryRouter
        initialEntries={["/workspaces/workspace/sessions/session_0"]}
      >
        <Routes>
          <Route
            path="/workspaces/:workspaceId/sessions"
            element={<ConversationsPage />}
          >
            <Route path=":sessionId" element={<p>Session detail</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(await screen.findByText("Question 18")).toBeTruthy();
  expect(screen.getByText("Answer 18")).toBeTruthy();
  expect(screen.getByText("Untitled session")).toBeTruthy();
  expect(screen.getByText("Open session")).toBeTruthy();
  expect(
    screen.getByRole("link", { name: /Question 0/ }).getAttribute("href"),
  ).toBe("/workspaces/workspace/sessions/session_0");
  expect(requests).toEqual(["/api/v1/workspaces/workspace/sessions"]);
  cleanup();
  cache.clear();
});

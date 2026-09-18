import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { createClient, type Client } from "../../service-client";
import type { Schema } from "../../shared/api";
import { ThreadQueue } from "./queue";

let client: Client;
vi.mock("../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "workspace" },
    basePath: "/workspace",
    can: (action: string) =>
      ["queued_submission.read", "queued_submission.delete"].includes(action),
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

it("deletes with a query precondition and refreshes after an empty 204 response", async () => {
  const requests: Request[] = [];
  let deleted = false;
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session", csrfToken: "csrf-fixture" },
    fetch: async (input, init) => {
      const request = new Request(input, init);
      requests.push(request);
      if (request.method === "DELETE") {
        deleted = true;
        return new Response(null, { status: 204 });
      }
      return Response.json({
        items: deleted
          ? []
          : [
              {
                queued_submission_id: "qsub_fixture",
                version: 3,
                state: "queued",
                created_at: "2026-09-01T00:00:00Z",
                submission: {
                  input: {
                    schema_version: "2",
                    content: [{ type: "text", text: "Queued input" }],
                  },
                },
              },
            ],
        next_cursor: null,
      });
    },
  });
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <ThreadQueue
          thread={
            {
              id: "thread",
              role: "root",
              session_purpose: "debug",
              session_id: "session",
              version: 1,
              queue_version: 1,
            } as Schema["ThreadResource"]
          }
          canConsume={false}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /Thread queue/ }));
  await user.click(await screen.findByRole("button", { name: "Delete" }));
  await user.click(
    await screen.findByRole("button", {
      name: "Delete queued message",
    }),
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(
    await screen.findByText("No messages in this queue state."),
  ).toBeTruthy();
  const removal = requests.find((request) => request.method === "DELETE")!;
  expect(new URL(removal.url).searchParams.get("expected_version")).toBe("3");
  expect(removal.body).toBeNull();
  expect(removal.headers.get("Idempotency-Key")).toBeTruthy();
  expect(removal.headers.get("X-A13N-Workspace-ID")).toBe("workspace");
  expect(
    requests.filter((request) => request.method === "DELETE"),
  ).toHaveLength(1);
  expect(
    requests.filter((request) => request.method === "GET").length,
  ).toBeGreaterThanOrEqual(2);
});

// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createClient, type Client } from "../../service-client";
import type { Schema } from "../../shared/api";
import { SessionEditor } from "./session-editor";
import { sessionPath } from "./session-identity";

let client: Client;
vi.mock("../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test" } }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(() => {
  cleanup();
  client?.close();
});
const session = {
  id: "sess_test",
  workspace_id: "ws_test",
  created_by_id: "usr_test",
  labels: { project: "Widgets" },
  title: "Review #42",
  origin: {
    application: "review-bot",
    name: "GitHub · acme/widgets",
    external_id: "main_42",
  },
  version: 1,
  run_count: 0,
  last_run_id: null,
  preview: null,
  created_at: "2026-10-21T00:00:00Z",
  updated_at: "2026-10-21T00:00:00Z",
} as Schema["SessionView"];

it("preserves an edit on conflict, reloads explicitly, then clears identity under the fresh ETag", async () => {
  const patches: { body: unknown; etag: string | null }[] = [];
  const close = vi.fn();
  const updated = { ...session, title: "Updated elsewhere", version: 2 };
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session", csrfToken: "csrf" },
    fetch: async (input, init) => {
      const request = new Request(input, init);
      if (request.method === "PATCH") {
        patches.push({
          body: await request.json(),
          etag: request.headers.get("If-Match"),
        });
        if (patches.length === 1)
          return Response.json(
            {
              error: {
                code: "precondition_failed",
                message: "Changed",
                details: {},
              },
            },
            { status: 412 },
          );
        return Response.json({
          ...updated,
          title: null,
          origin: null,
          version: 3,
        });
      }
      return Response.json(updated);
    },
  });
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <SessionEditor session={session} onClose={close} />
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await user.clear(screen.getByRole("textbox", { name: "Session title" }));
  await user.type(
    screen.getByRole("textbox", { name: "Session title" }),
    "My draft",
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByText("This session changed");
  expect(
    (screen.getByRole("textbox", { name: "Session title" }) as HTMLInputElement)
      .value,
  ).toBe("My draft");
  expect(close).not.toHaveBeenCalled();
  await user.click(
    screen.getByRole("button", { name: "Reload saved details" }),
  );
  await waitFor(() =>
    expect(
      (
        screen.getByRole("textbox", {
          name: "Session title",
        }) as HTMLInputElement
      ).value,
    ).toBe("Updated elsewhere"),
  );
  await user.clear(screen.getByRole("textbox", { name: "Session title" }));
  await user.clear(screen.getByRole("textbox", { name: "Source application" }));
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(patches[0].etag).toBe('"sess_test:1"');
  expect(patches[1]).toEqual({
    body: { title: null, origin: null },
    etag: '"sess_test:2"',
  });
  cache.clear();
});

it("opens only exact Thread IDs", () => {
  expect(sessionPath("/workspace/ws", "sess_one", "fix deployment")).toBe(
    "/workspace/ws/sessions/sess_one",
  );
  expect(
    sessionPath(
      "/workspace/ws",
      "sess_one",
      "thread_0123456789abcdef0123456789abcdef",
    ),
  ).toBe(
    "/workspace/ws/sessions/sess_one/threads/thread_0123456789abcdef0123456789abcdef",
  );
  expect(sessionPath("/workspace/ws", "sess_one", "thread_external_name")).toBe(
    "/workspace/ws/sessions/sess_one",
  );
});

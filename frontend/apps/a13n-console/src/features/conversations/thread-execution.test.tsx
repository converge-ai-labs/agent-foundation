import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createClient, type Client } from "../../service-client";
import { fixtureThread } from "./transcript/fixture";
import { ThreadExecutionDialog } from "./thread-execution";

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

it("clears inherited leaves under the original ETag and reloads a conflicting policy", async () => {
  const thread = fixtureThread({
    id: "thr_test",
    version: 1,
    execution: {
      tools: { mode: "auto", foreground_wait_seconds: 5 },
      steering: "immediate",
    },
  });
  const patches: { body: unknown; etag: string | null }[] = [];
  const close = vi.fn();
  const updated = {
    ...thread,
    version: 2,
    execution: { steering: "boundary" as const },
  };
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
        return Response.json({ ...updated, execution: null, version: 3 });
      }
      return Response.json(updated);
    },
  });
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <ThreadExecutionDialog thread={thread} onClose={close} />
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("combobox", { name: "Tool execution" }));
  await user.click(screen.getByRole("option", { name: "Inherit" }));
  await user.clear(
    screen.getByRole("spinbutton", { name: "Foreground wait (seconds)" }),
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByText("This thread changed");
  expect(patches[0]).toEqual({
    etag: '"thr_test:1"',
    body: {
      execution: {
        tools: { mode: null, foreground_wait_seconds: null },
        steering: "immediate",
      },
    },
  });
  expect(close).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Reload saved policy" }));
  await screen.findByText("At the next boundary");
  await user.click(screen.getByRole("combobox", { name: "Steering" }));
  await user.click(screen.getByRole("option", { name: "Inherit" }));
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
  expect(patches[1]).toEqual({
    etag: '"thr_test:2"',
    body: { execution: null },
  });
  cache.clear();
});

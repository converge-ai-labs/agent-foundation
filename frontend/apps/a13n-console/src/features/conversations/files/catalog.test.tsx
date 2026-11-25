// @vitest-environment jsdom
import {
  act,
  cleanup,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { createClient, type Client } from "../../../service-client";
import { item } from "../display-fixture";
import { SessionDisplays } from "../session-displays";
import { useConversationFiles } from "./catalog";
import FilesPanel from "./panel";
import type { FileReference } from "./assets";

let client: Client;
let displays: SessionDisplays;
vi.mock("../../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "workspace" } }),
}));
vi.mock("../session-stream", () => ({
  useSessionStream: () => ({ displays }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(() => {
  cleanup();
  displays.close();
  client.close();
});

it("retains a streamed publication when a successor starts before the first run's items refresh", async () => {
  displays = new SessionDisplays();
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
    fetch: async (input, init) => {
      const path = new URL(new Request(input, init).url).pathname;
      const second = path.includes("/second/");
      if (path.endsWith("/lineage"))
        return Response.json({
          items: (second ? ["second", "first"] : ["first"]).map((id) => ({
            id,
          })),
          next_cursor: null,
        });
      const asset = { type: "asset", asset_id: "ast_upload" };
      return Response.json({
        complete: second,
        items: [],
        run: { input: { content: second ? [] : [asset] } },
      });
    },
  });
  const cache = new QueryClient();
  const { result, rerender } = renderHook(
    ({ runId }) => useConversationFiles(runId),
    {
      initialProps: { runId: "first" },
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={cache}>{children}</QueryClientProvider>
      ),
    },
  );
  await waitFor(() =>
    expect(result.current.files).toEqual([{ assetId: "ast_upload" }]),
  );
  act(() => {
    displays.items({
      thread_id: "thread",
      run_id: "first",
      first: 1,
      complete: true,
      continuation: null,
      items: [
        item("published", "observation", {
          name: "a13n.harness.tool.asset_published",
          subagentRunId: "inline-child",
          value: {
            event: {
              payload: {
                tool_id: "service.publish_asset",
                tool_call_id: "call",
                value: { asset_id: "ast_report" },
              },
            },
          },
        }),
      ],
    });
  });
  const publication = {
    assetId: "ast_report",
    publication: { runId: "first", toolCallId: "call", legacy: false },
  };
  await waitFor(() => expect(result.current.files).toContainEqual(publication));
  rerender({ runId: "second" });
  expect(result.current.files).toContainEqual(publication);
  await waitFor(() => expect(result.current.loading).toBe(false));
  expect(result.current.files).toContainEqual(publication);
});

it("shows that the file list may be incomplete when a saved observation was omitted", async () => {
  displays = new SessionDisplays();
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
    fetch: async (input, init) => {
      const path = new URL(new Request(input, init).url).pathname;
      if (path.endsWith("/lineage"))
        return Response.json({ items: [{ id: "first" }], next_cursor: null });
      return Response.json({
        complete: true,
        items: [item("omitted-observation", "observation", { omitted: true })],
        run: { input: null },
      });
    },
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <FilesPanel runId="first" onSelect={vi.fn()} onClose={vi.fn()} />
    </QueryClientProvider>,
  );
  const notice = await screen.findByText(
    "Some run content is unavailable, so this file list may be incomplete.",
  );
  expect(notice.getAttribute("role")).toBe("status");
  expect(screen.queryByText("No files found")).toBeNull();
});

it("replaces the file list with a preview and restores row focus on back", async () => {
  displays = new SessionDisplays();
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(new Request(input, init).url).pathname;
    if (path.endsWith("/lineage"))
      return Response.json({ items: [{ id: "first" }], next_cursor: null });
    if (path.endsWith("/items"))
      return Response.json({
        complete: true,
        items: [],
        run: {
          input: { content: [{ type: "asset", asset_id: "ast_report" }] },
        },
      });
    if (path.endsWith("/content")) return new Response("# Weekly review");
    return Response.json({
      id: "ast_report",
      name: "report.md",
      size: 15,
      content_type: "text/markdown",
      digest: "file",
    });
  });
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
    fetch,
  });
  function Panel() {
    const [selected, setSelected] = useState<FileReference>();
    return (
      <FilesPanel
        runId="first"
        selected={selected}
        onSelect={setSelected}
        onClose={vi.fn()}
      />
    );
  }
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Panel />
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  const row = await screen.findByRole("button", { name: /report.md/ });
  expect(
    fetch.mock.calls.some(([input, init]) =>
      new Request(input, init).url.endsWith("/content"),
    ),
  ).toBe(false);
  await user.click(row);
  expect(
    await screen.findByRole("heading", { name: "Weekly review" }),
  ).toBeTruthy();
  expect(screen.queryByRole("list", { name: "Conversation files" })).toBeNull();
  const back = screen.getByRole("button", { name: "Back to files" });
  expect(document.activeElement).toBe(back);
  await user.click(back);
  expect(document.activeElement).toBe(
    screen.getByRole("button", { name: /report.md/ }),
  );
  expect(screen.queryByRole("heading", { name: "Weekly review" })).toBeNull();
});

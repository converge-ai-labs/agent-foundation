// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { createClient, type Client } from "../../../service-client";
import { item } from "../display-fixture";
import { SessionDisplays } from "../session-displays";
import { useConversationFiles } from "./catalog";

let client: Client;
let displays: SessionDisplays;
vi.mock("../../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "workspace" } }),
}));
vi.mock("../session-stream", () => ({
  useSessionStream: () => ({ displays }),
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

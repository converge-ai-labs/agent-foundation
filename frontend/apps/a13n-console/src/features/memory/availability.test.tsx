import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { eligibleMemoryProvider, useMemoryProviders } from "./availability";

const state = vi.hoisted(() => ({
  workspace: "ws_one",
  canRead: true,
  GET: vi.fn(),
}));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http: state }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: state.workspace },
    can: () => state.canRead,
  }),
}));
const provider = {
  id: "memprov_one",
  enabled: true,
  credential_configured: true,
  workspace_id: null,
};
const response = (items: unknown[], next_cursor: string | null = null) => ({
  data: { items, next_cursor },
  response: new Response(),
});
function setup() {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  const hook = renderHook(() => useMemoryProviders(), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={cache}>{children}</QueryClientProvider>
    ),
  });
  return { ...hook, cache };
}
beforeEach(() => {
  state.workspace = "ws_one";
  state.canRead = true;
  state.GET.mockReset();
});
it.each([
  ["empty", [], false],
  ["disabled", [{ ...provider, enabled: false }], true],
  [
    "missing credentials",
    [{ ...provider, credential_configured: false }],
    true,
  ],
  ["inherited configured", [provider], true],
])(
  "uses %s provider state, not installed adapter definitions",
  async (_, items, visible) => {
    state.GET.mockResolvedValue(response(items as unknown[]));
    const { result } = setup();
    await waitFor(() => expect(result.current.providers.isSuccess).toBe(true));
    expect(result.current.visible).toBe(visible);
    expect(state.GET.mock.calls[0][0]).toBe(
      "/api/v1/workspaces/{workspace}/memory-providers",
    );
  },
);
it("does not flash an entry before the initial catalog resolves", async () => {
  let resolve!: (value: ReturnType<typeof response>) => void;
  state.GET.mockReturnValue(
    new Promise((done) => {
      resolve = done;
    }),
  );
  const { result } = setup();
  expect(result.current.visible).toBe(false);
  await act(async () => resolve(response([])));
  expect(result.current.visible).toBe(false);
});
it("traverses the catalog and refreshes after provider mutations", async () => {
  state.GET.mockResolvedValueOnce(response([], "page-two"));
  state.GET.mockResolvedValueOnce(response([provider]));
  const { result, cache } = setup();
  await waitFor(() => expect(result.current.visible).toBe(true));
  expect(state.GET.mock.calls[1][1].params.query.cursor).toBe("page-two");
  state.GET.mockResolvedValue(response([]));
  await act(async () => {
    await cache.invalidateQueries({ queryKey: ["memory-providers"] });
  });
  await waitFor(() => expect(result.current.visible).toBe(false));
});
it("does not reuse another workspace's availability while its catalog loads", async () => {
  state.GET.mockResolvedValue(response([provider]));
  const { result, rerender } = setup();
  await waitFor(() => expect(result.current.visible).toBe(true));
  state.workspace = "ws_two";
  state.GET.mockResolvedValue(response([]));
  rerender();
  expect(result.current.visible).toBe(false);
  await waitFor(() => expect(result.current.providers.isSuccess).toBe(true));
  expect(result.current.visible).toBe(false);
  expect(state.GET.mock.calls.at(-1)?.[1].params.path.workspace).toBe("ws_two");
});
it("keeps recovery available after a catalog failure", async () => {
  state.GET.mockRejectedValue(new Error("Network unavailable"));
  const { result } = setup();
  await waitFor(() => expect(result.current.providers.isError).toBe(true));
  expect(result.current.visible).toBe(true);
});
it("does not equate missing catalog permission with missing subject access", () => {
  state.canRead = false;
  const { result } = setup();
  expect(result.current.visible).toBe(true);
  expect(state.GET).not.toHaveBeenCalled();
});

const conditional = {
  type: "custom_memory",
  configuration_schema: {
    type: "object",
    properties: { access: { type: "string", default: "public" } },
  },
  authentication: {
    mode: "required" as const,
    cases: [{ field: "access", equals: "public", mode: "forbidden" as const }],
  },
};
it.each([
  ["forbidden without a credential", { access: "public" }, false, true],
  ["forbidden with a stored credential", { access: "public" }, true, false],
  ["required without a credential", { access: "private" }, false, false],
  ["required with a credential", { access: "private" }, true, true],
])(
  "resolves declared authentication for %s",
  (_, configuration, credential_configured, expected) => {
    expect(
      eligibleMemoryProvider(
        {
          ...provider,
          type: "custom_memory",
          configuration,
          credential_configured,
        } as never,
        [conditional] as never,
      ),
    ).toBe(expected);
  },
);

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { useAgentChoices } from "./choices";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ workspace: () => ({ GET: get }) }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test" } }),
}));

it("keeps models available when the connections lookup fails", async () => {
  const model = {
    key: "test-model",
    name: "Test model",
    enabled: true,
    config: { model_name: "test" },
  };
  get.mockImplementation(async (path: string) => {
    if (path === "/api/v1/connections")
      throw new Error("Connection lookup failed");
    return {
      data: {
        items: path === "/api/v1/models" ? [model] : [],
        next_cursor: null,
      },
    };
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const { result } = renderHook(useAgentChoices, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  });
  await waitFor(() => {
    expect(result.current.connections.isError).toBe(true);
    expect(result.current.data.models).toEqual([model]);
  });
  expect(result.current.models.isError).toBe(false);
});

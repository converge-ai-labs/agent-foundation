// @vitest-environment jsdom
import { render, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";
import { MCPConnectionIcon } from "./mcp-icon";

const get = vi.hoisted(() => vi.fn());
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: { GET: get }, workspace: () => ({ GET: get }) }),
}));

it("reuses the catalog logo for a saved remote MCP connection", async () => {
  get.mockResolvedValue({
    data: {
      items: [
        {
          key: "zoom",
          url: "https://mcp.zoom.us/mcp/zoom/streamable",
          logo_url: "https://cdn.simpleicons.org/zoom",
        },
      ],
      next_cursor: null,
    },
    response: new Response(),
  });
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const { container } = render(
    <QueryClientProvider client={cache}>
      <MCPConnectionIcon endpoint="https://mcp.zoom.us/mcp/zoom/streamable" />
    </QueryClientProvider>,
  );
  await waitFor(() =>
    expect(container.querySelector("img")?.src).toBe(
      "https://cdn.simpleicons.org/zoom",
    ),
  );
  cache.clear();
});

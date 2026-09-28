import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { createClient, type Client } from "../../service-client";
import { AgentAvatar } from "./avatar";

let client: Client | undefined;
vi.mock("../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test" } }),
}));
afterEach(() => {
  cleanup();
  client?.close();
  client = undefined;
  vi.restoreAllMocks();
});

it("keeps an existing agent's color when renamed or remounted", () => {
  const { rerender, unmount } = render(
    <AgentAvatar id="agt_stable" name="Alpha" />,
  );
  const color = screen.getByText("A").style.backgroundColor;
  rerender(<AgentAvatar id="agt_stable" name="Beta" />);
  expect(screen.getByText("B").style.backgroundColor).toBe(color);
  unmount();
  render(<AgentAvatar id="agt_stable" name="Beta" />);
  expect(screen.getByText("B").style.backgroundColor).toBe(color);
});

it("previews name changes and handles non-Latin and empty names", () => {
  const { rerender } = render(<AgentAvatar name="  alpha" />);
  expect(screen.getByText("A")).toBeDefined();
  rerender(<AgentAvatar name="研究助手" />);
  expect(screen.getByText("研")).toBeDefined();
  rerender(<AgentAvatar name="   " />);
  expect(screen.getByText("A")).toBeDefined();
});

it("loads the avatar with workspace authentication and releases its blob URL", async () => {
  const requests: Request[] = [];
  client = createClient({
    baseUrl: "https://service.example/proxy",
    auth: { type: "session" },
    fetch: async (input, init) => {
      requests.push(new Request(input, init));
      return new Response("image", {
        headers: { "Content-Type": "image/png" },
      });
    },
  });
  const createUrl = vi
    .spyOn(URL, "createObjectURL")
    .mockReturnValue("blob:avatar");
  const revokeUrl = vi
    .spyOn(URL, "revokeObjectURL")
    .mockImplementation(() => {});
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  const view = render(
    <QueryClientProvider client={cache}>
      <AgentAvatar name="Research" url="/api/v1/agents/agt_test/image?v=2" />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(createUrl).toHaveBeenCalledOnce());
  expect(requests).toHaveLength(1);
  expect(requests[0].url).toBe(
    "https://service.example/proxy/api/v1/agents/agt_test/image?v=2",
  );
  expect(requests[0].headers.get("X-Workspace-ID")).toBe("ws_test");
  expect(requests[0].credentials).toBe("same-origin");
  expect(createUrl.mock.calls[0][0]).toHaveProperty("type", "image/png");
  view.unmount();
  expect(revokeUrl).toHaveBeenCalledWith("blob:avatar");
  cache.clear();
});

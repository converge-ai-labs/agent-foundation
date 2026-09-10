import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { WorkspaceProvider, useWorkspace } from "./workspace";

const mocks = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock("../auth/context", () => ({
  useAuth: () => ({
    isPending: false,
    error: null,
    data: {
      organizations: [{ id: "org_test", key: "acme", name: "Acme" }],
      user: { value: { id: "usr_test" } },
    },
  }),
  useClient: () => ({ http: { GET: mocks.GET } }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

function CurrentWorkspace() {
  const { workspace, basePath } = useWorkspace();
  return <p>{`${useLocation().pathname}|${workspace.id}|${basePath}`}</p>;
}
function mount(path: string) {
  mocks.GET.mockImplementation(async (route: string) => {
    if (route.endsWith("/workspaces"))
      return {
        data: {
          items: [
            { id: "ws_first", key: "research", name: "Research" },
            { id: "ws_second", key: "design", name: "Design" },
          ],
          next_cursor: null,
        },
      };
    if (route.endsWith("/permissions"))
      return { data: { actions: ["agent.read"], organization_admin: false } };
    throw new Error(`Unexpected route: ${route}`);
  });
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="*" element={<h1>Not found</h1>} />
          <Route
            path="/workspace/:workspaceKey/*"
            element={
              <WorkspaceProvider>
                <CurrentWorkspace />
              </WorkspaceProvider>
            }
          />
          <Route
            path="/providers"
            element={
              <WorkspaceProvider>
                <CurrentWorkspace />
              </WorkspaceProvider>
            }
          />
          <Route
            path="/"
            element={
              <WorkspaceProvider>
                <CurrentWorkspace />
              </WorkspaceProvider>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

it("selects the exact workspace key and retains its immutable identity", async () => {
  mount("/workspace/design/agents");
  expect(
    await screen.findByText(
      "/workspace/design/agents|ws_second|/workspace/design",
    ),
  ).toBeTruthy();
});
it.each([
  "/other/design/agents",
  "/workspace/missing/agents",
  "/workspace/ws_first/agents",
  "/org_test/research/agents",
])("does not fall back to an accessible workspace for %s", async (path) => {
  mount(path);
  expect(
    await screen.findByRole("heading", { name: "Not found" }),
  ).toBeTruthy();
  expect(screen.queryByText(/\|ws_/)).toBeNull();
});
it("redirects the entry page using the current workspace key", async () => {
  mount("/");
  expect(
    await screen.findByText(
      /\/workspace\/(research|design)\/agents\|ws_(first|second)\|\/workspace\//,
    ),
  ).toBeTruthy();
});

it("preserves the selected workspace when provider management opens in another tab", async () => {
  mount("/providers?workspace=design");
  expect(
    await screen.findByText("/providers|ws_second|/workspace/design"),
  ).toBeTruthy();
});

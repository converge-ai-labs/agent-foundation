import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { WorkspaceProvider, useWorkspace } from "./workspace";

const mocks = vi.hoisted(() => ({ GET: vi.fn(), userId: "usr_test" }));
vi.mock("../auth/context", () => ({
  useAuth: () => ({
    isPending: false,
    error: null,
    data: {
      organizations: [
        { id: "org_test", key: "acme", name: "Acme", permissions: [] },
      ],
      user: { value: { id: mocks.userId } },
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
function mount(
  path: string,
  {
    workspacesError,
    firstArchived,
  }: { workspacesError?: Error; firstArchived?: boolean } = {},
) {
  mocks.GET.mockImplementation(async (route: string) => {
    if (route.endsWith("/workspaces")) {
      if (workspacesError) throw workspacesError;
      return {
        data: {
          items: [
            {
              id: "ws_first",
              key: "research",
              name: "Research",
              permissions: ["read"],
              archived_at: firstArchived ? "2026-09-24T00:00:00Z" : null,
            },
            {
              id: "ws_second",
              key: "design",
              name: "Design",
              permissions: ["read"],
            },
          ],
          next_cursor: null,
        },
      };
    }
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
it.each(["/workspace/missing/agents", "/workspace/ws_first/agents"])(
  "does not fall back to an accessible workspace for %s",
  async (path) => {
    mount(path);
    expect(
      await screen.findByRole("heading", { name: "Not found" }),
    ).toBeTruthy();
    expect(screen.queryByText(/\|ws_/)).toBeNull();
  },
);
it("redirects the entry page using the current workspace key", async () => {
  mount("/");
  expect(
    await screen.findByText(
      /\/workspace\/(research|design)\/agents\|ws_(first|second)\|\/workspace\//,
    ),
  ).toBeTruthy();
});

it("enters a workspace that still runs rather than an archived one", async () => {
  // A user who has not entered a workspace yet, so none is remembered.
  mocks.userId = "usr_first_visit";
  try {
    mount("/", { firstArchived: true });
    expect(
      await screen.findByText(
        "/workspace/design/agents|ws_second|/workspace/design",
      ),
    ).toBeTruthy();
  } finally {
    mocks.userId = "usr_test";
  }
});

it("preserves the selected workspace when provider management opens in another tab", async () => {
  mount("/workspace/design/settings?section=providers");
  expect(
    await screen.findByText(
      "/workspace/design/settings|ws_second|/workspace/design",
    ),
  ).toBeTruthy();
});

it("offers retry and personal settings when workspaces fail", async () => {
  mount("/workspace/design/agents", {
    workspacesError: new Error("Workspaces unavailable"),
  });

  expect(
    await screen.findByRole("heading", { name: "Workspace unavailable" }),
  ).toBeTruthy();
  expect(
    screen
      .getByRole("link", { name: "Personal settings" })
      .getAttribute("href"),
  ).toBe("/settings/profile");

  const workspaceCalls = () =>
    mocks.GET.mock.calls.filter(([route]) =>
      String(route).endsWith("/workspaces"),
    ).length;
  const beforeRetry = workspaceCalls();
  await userEvent.click(screen.getByRole("button", { name: "Try again" }));
  await waitFor(() => expect(workspaceCalls()).toBeGreaterThan(beforeRetry));
});

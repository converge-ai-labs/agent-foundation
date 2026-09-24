import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ProvidersPage } from "./page";

const state = vi.hoisted(() => ({
  GET: vi.fn(),
  organizationWrite: true,
  hasWorkspace: true,
  manage: true,
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: { GET: state.GET } }),
}));
vi.mock("../../layout/workspace", () => ({
  useAccess: () => ({
    workspace: state.hasWorkspace
      ? { id: "ws_test", key: "research", name: "Research" }
      : undefined,
    organization: { id: "org_test", name: "Acme" },
    organizationCan: () => state.organizationWrite,
    can: () => state.manage,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
function Location() {
  return <output>{useLocation().search}</output>;
}
function mount(
  query = "category=web",
  kind: "workspace" | "organization" = "workspace",
) {
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false, gcTime: 0 } },
        })
      }
    >
      <MemoryRouter
        initialEntries={[
          `/${kind === "workspace" ? "workspace/research" : "organization"}/settings?${query}`,
        ]}
      >
        <ProvidersPage
          scope={{ kind, id: kind === "workspace" ? "ws_test" : "org_test" }}
        />
        <Location />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  state.GET.mockResolvedValue({ data: { items: [], next_cursor: null } });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  HTMLElement.prototype.scrollIntoView = () => {};
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.resetAllMocks();
  state.organizationWrite = true;
  state.hasWorkspace = true;
  state.manage = true;
});
it("restores category and scope, then switches domains without losing workspace context", async () => {
  const user = userEvent.setup();
  mount();
  await screen.findByText("No web providers yet");
  expect(state.GET).toHaveBeenCalledWith(
    "/api/v1/organizations/{organization_id}/web-providers",
    expect.objectContaining({
      params: {
        path: { organization_id: "org_test" },
        query: expect.objectContaining({ workspace_id: "ws_test" }),
      },
    }),
  );
  await user.click(screen.getByRole("tab", { name: "Connector" }));
  await screen.findByText("No connector providers yet");
  expect(screen.getByRole("status").textContent).toContain(
    "category=connectors",
  );
  expect(screen.queryByRole("combobox", { name: "Scope" })).toBeNull();
});
it("allows an organization writer to manage shared providers without a workspace", async () => {
  state.hasWorkspace = false;
  mount("category=web", "organization");
  await screen.findByText("No web providers yet");
  expect(screen.getByRole("button", { name: "Add provider" })).toBeTruthy();
  expect(
    state.GET.mock.calls.every(
      ([path, init]) =>
        !path.includes("/workspaces/") && !init.params.query?.workspace_id,
    ),
  ).toBe(true);
});
it("does not query organization resources when organization access is unavailable", () => {
  state.organizationWrite = false;
  mount("category=web", "organization");
  expect(screen.getByText("Access unavailable")).toBeTruthy();
  expect(state.GET).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "Add provider" })).toBeNull();
});
it("shows workspace providers without offering mutations to a read-only member", async () => {
  state.organizationWrite = false;
  state.manage = false;
  mount();
  await screen.findByText("No web providers yet");
  expect(screen.queryByRole("button", { name: "Add provider" })).toBeNull();
});

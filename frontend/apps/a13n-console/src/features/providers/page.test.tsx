import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ProvidersPage } from "./page";

const state = vi.hoisted(() => ({
  GET: vi.fn(),
  organizationAdmin: true,
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
    organizationAdmin: state.organizationAdmin,
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
  query = "section=providers&category=search",
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
  state.organizationAdmin = true;
  state.hasWorkspace = true;
  state.manage = true;
});
it("restores category and scope, then switches domains without losing workspace context", async () => {
  const user = userEvent.setup();
  mount();
  await screen.findByText("No search providers yet");
  expect(state.GET).toHaveBeenCalledWith(
    "/api/v1/workspaces/{workspace}/search-providers",
    expect.objectContaining({
      params: expect.objectContaining({ path: { workspace: "ws_test" } }),
    }),
  );
  await user.click(screen.getByRole("tab", { name: "Connector" }));
  await screen.findByText("No connector providers");
  expect(screen.getByRole("status").textContent).toContain(
    "section=providers&category=connectors",
  );
  expect(screen.queryByRole("combobox", { name: "Scope" })).toBeNull();
});
it("allows an organization administrator to manage providers without a workspace", async () => {
  state.hasWorkspace = false;
  mount("section=providers&category=search", "organization");
  await screen.findByText("No search providers yet");
  expect(
    screen.getByRole("button", { name: "Add search provider" }),
  ).toBeTruthy();
  expect(
    state.GET.mock.calls.every(([path]) => !path.includes("/workspaces/")),
  ).toBe(true);
});
it("does not query organization resources when organization access is unavailable", () => {
  state.organizationAdmin = false;
  mount("section=providers&category=search", "organization");
  expect(screen.getByText("Access unavailable")).toBeTruthy();
  expect(state.GET).not.toHaveBeenCalled();
  expect(
    screen.queryByRole("button", { name: "Add search provider" }),
  ).toBeNull();
});
it("shows workspace providers without offering mutations to a read-only member", async () => {
  state.organizationAdmin = false;
  state.manage = false;
  mount();
  await screen.findByText("No search providers yet");
  expect(
    screen.queryByRole("button", { name: "Add search provider" }),
  ).toBeNull();
});

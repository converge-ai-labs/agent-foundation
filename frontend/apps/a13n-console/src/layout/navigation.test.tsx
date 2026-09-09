// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, Navigate } from "react-router";
import { Shell } from "./shell";
import { SettingsLayout } from "../features/settings/layout";

vi.mock("../auth/context", () => ({
  useAuth: () => ({
    data: { user: { value: { name: "Alex", email: "alex@example.com" } } },
    logout: vi.fn(),
  }),
}));
const access = vi.hoisted(() => ({ organizationAdmin: false }));
vi.mock("./workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "workspace", name: "Design" },
    organization: { name: "Organization" },
    workspaces: [{ id: "workspace", name: "Design" }],
    organizationAdmin: access.organizationAdmin,
    can: () => true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
beforeEach(() =>
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  ),
);
beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn();
});
afterEach(() => {
  cleanup();
  access.organizationAdmin = false;
  vi.unstubAllGlobals();
});

it("replaces the main sidebar with settings navigation and restores it on return", async () => {
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={["/workspaces/workspace/agents"]}>
        <Routes>
          <Route path="/workspaces/:workspaceId" element={<Shell />}>
            <Route path="agents" element={<h1>Agent directory</h1>} />
            <Route
              path="settings"
              element={
                <SettingsLayout
                  scope="workspace"
                  content={{
                    profile: <p>Workspace profile</p>,
                    members: <p>Workspace members</p>,
                  }}
                />
              }
            />
          </Route>
          <Route
            path="/settings/profile"
            element={<Navigate to="/workspaces/workspace/settings" replace />}
          />
          <Route path="/" element={<Shell />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(
    screen.getByRole("complementary", { name: "Main navigation" }),
  ).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Workspace menu" }));
  await user.click(screen.getByRole("menuitem", { name: "Settings" }));
  expect(
    screen.queryByRole("complementary", { name: "Main navigation" }),
  ).toBeNull();
  expect(
    screen.getByRole("navigation", { name: "Settings navigation" }),
  ).toBeTruthy();
  expect(screen.getAllByRole("complementary")).toHaveLength(1);
  await user.click(screen.getByRole("link", { name: "Members" }));
  expect(screen.getByText("Workspace members")).toBeTruthy();
  expect(screen.queryByText("Workspace profile")).toBeNull();
  await user.click(screen.getByRole("link", { name: "Back to workspace" }));
  expect(
    screen.getByRole("complementary", { name: "Main navigation" }),
  ).toBeTruthy();
  expect(
    screen.queryByRole("navigation", { name: "Settings navigation" }),
  ).toBeNull();
});

it("keeps resource categories in sidebar links and restores the selected category from its URL", async () => {
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={["/workspaces/workspace/models/providers"]}>
        <Routes>
          <Route path="/workspaces/:workspaceId" element={<Shell />}>
            <Route path="models" element={<p>Model directory</p>} />
            <Route
              path="models/providers"
              element={<p>Provider directory</p>}
            />
            <Route path="environments" element={<p>Template directory</p>} />
            <Route
              path="environments/instances"
              element={<p>Instance directory</p>}
            />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(
    screen
      .getByRole("link", { name: "Providers" })
      .getAttribute("aria-current"),
  ).toBe("page");
  await user.click(screen.getByRole("link", { name: "All models" }));
  expect(screen.getByText("Model directory")).toBeTruthy();
  await user.click(screen.getByRole("link", { name: "Environments" }));
  await user.click(screen.getByRole("link", { name: "Instances" }));
  expect(screen.getByText("Instance directory")).toBeTruthy();
  expect(
    screen
      .getByRole("link", { name: "Instances" })
      .getAttribute("aria-current"),
  ).toBe("page");
  expect(screen.queryByRole("tablist")).toBeNull();
});

it("keeps workspace switching available with one workspace and marks the current item", async () => {
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={["/workspaces/workspace/agents"]}>
        <Routes>
          <Route path="/workspaces/:workspaceId/*" element={<Shell />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await user.click(screen.getByRole("button", { name: "Workspace menu" }));
  expect(screen.getAllByRole("menuitem", { name: "Settings" })).toHaveLength(1);
  const switcher = screen.getByRole("menuitem", { name: "Switch workspace" });
  switcher.focus();
  await user.keyboard("{ArrowRight}");
  expect(
    screen
      .getByRole("menuitem", { name: "Design" })
      .getAttribute("aria-current"),
  ).toBe("true");
});

it("shows all authorized settings groups and searches without changing the selected page", async () => {
  access.organizationAdmin = true;
  const user = userEvent.setup();
  render(
    <MemoryRouter
      initialEntries={["/workspaces/workspace/settings?section=profile"]}
    >
      <SettingsLayout
        scope="workspace"
        content={{ profile: <p>Workspace profile</p> }}
      />
    </MemoryRouter>,
  );
  const navigation = within(
    screen.getByRole("navigation", { name: "Settings navigation" }),
  );
  for (const name of ["Personal", "Workspace", "Organization"])
    expect(navigation.getByRole("heading", { name })).toBeTruthy();
  await user.type(
    screen.getByRole("searchbox", { name: "Search settings" }),
    "password",
  );
  expect(screen.getByText("No matching settings")).toBeTruthy();
  expect(screen.getByText("Workspace profile")).toBeTruthy();
  await user.clear(screen.getByRole("searchbox", { name: "Search settings" }));
  await user.type(
    screen.getByRole("searchbox", { name: "Search settings" }),
    "members",
  );
  expect(navigation.getAllByRole("link")).toHaveLength(2);
});

it("collapses the settings navigation after selecting a section", async () => {
  const user = userEvent.setup();
  render(
    <MemoryRouter initialEntries={["/settings/profile?section=profile"]}>
      <SettingsLayout
        scope="personal"
        content={{
          profile: <p>Profile form</p>,
          security: <p>Security form</p>,
        }}
      />
    </MemoryRouter>,
  );
  const toggle = screen.getByRole("button", { name: "Settings" });
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  await user.click(toggle);
  expect(toggle.getAttribute("aria-expanded")).toBe("true");
  await user.click(screen.getByRole("link", { name: "Security" }));
  expect(toggle.getAttribute("aria-expanded")).toBe("false");
  expect(screen.getByText("Security form")).toBeTruthy();
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { SettingsLayout } from "../features/settings/layout";
import { Shell } from "./shell";

vi.mock("../auth/context", () => ({
  useAuth: () => ({
    data: { user: { value: { name: "Alex", email: "alex@example.com" } } },
    logout: vi.fn(),
  }),
}));
const access = vi.hoisted(() => ({ organizationAdmin: false }));
vi.mock("./workspace", () => {
  const useAccess = () => ({
    basePath: "/workspace/design",
    workspace: { id: "workspace", key: "design", name: "Design" },
    organization: { key: "acme", name: "Organization" },
    workspaces: [{ id: "workspace", key: "design", name: "Design" }],
    organizationCan: () => access.organizationAdmin,
    can: () => true,
  });
  return { useWorkspace: useAccess, useAccess };
});
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

it.each([
  ["Workspace menu", "Workspace settings", "Workspace general"],
  ["Alex", "Profile", "Personal profile"],
])(
  "opens settings from %s at its own scope and restores navigation on return",
  async (trigger, entry, landing) => {
    const user = userEvent.setup();
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter initialEntries={["/workspace/design/agents"]}>
          <Routes>
            <Route path="/workspace/:workspaceKey" element={<Shell />}>
              <Route path="agents" element={<h1>Agent directory</h1>} />
              <Route
                path="settings/:section?"
                element={
                  <SettingsLayout
                    scope="workspace"
                    content={{
                      general: <p>Workspace general</p>,
                      members: <p>Workspace members</p>,
                    }}
                  />
                }
              />
            </Route>
            <Route
              path="/settings/:section?"
              element={
                <SettingsLayout
                  scope="personal"
                  content={{ profile: <p>Personal profile</p> }}
                />
              }
            />
            <Route path="/" element={<Shell />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(
      screen.getByRole("complementary", { name: "Main navigation" }),
    ).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "Settings" }).getAttribute("href"),
    ).toBe("/workspace/design/settings");
    await user.hover(
      screen.getByRole("button", { name: new RegExp(`${trigger}$`) }),
    );
    await user.click(await screen.findByRole("menuitem", { name: entry }));
    expect(screen.getByText(landing)).toBeTruthy();
    expect(
      screen.queryByRole("complementary", { name: "Main navigation" }),
    ).toBeNull();
    expect(
      screen.getByRole("navigation", { name: "Settings navigation" }),
    ).toBeTruthy();
    expect(screen.getAllByRole("complementary")).toHaveLength(1);
    await user.click(
      within(
        screen.getByRole("navigation", { name: "Settings navigation" }),
      ).getByRole("link", { name: "Members" }),
    );
    expect(screen.getByText("Workspace members")).toBeTruthy();
    expect(screen.queryByText("Workspace general")).toBeNull();
    await user.click(screen.getByRole("link", { name: "Back to workspace" }));
    expect(
      screen.getByRole("complementary", { name: "Main navigation" }),
    ).toBeTruthy();
    expect(
      screen.queryByRole("navigation", { name: "Settings navigation" }),
    ).toBeNull();
  },
);

it("keeps resource categories in sidebar links and restores the selected category from its URL", async () => {
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={["/workspace/design/models"]}>
        <Routes>
          <Route path="/workspace/:workspaceKey" element={<Shell />}>
            <Route path="models" element={<p>Model directory</p>} />
            <Route path="memories" element={<p>Memory directory</p>} />
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
  expect(screen.queryByRole("link", { name: "Providers" })).toBeNull();
  await user.click(screen.getByRole("link", { name: "Models" }));
  expect(screen.getByText("Model directory")).toBeTruthy();
  await user.click(screen.getByRole("link", { name: "Memories" }));
  expect(screen.getByText("Memory directory")).toBeTruthy();
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

it("lists workspaces in the switcher and marks the current one", async () => {
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={["/workspace/design/agents"]}>
        <Routes>
          <Route path="/workspace/:workspaceKey/*" element={<Shell />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await user.click(screen.getByRole("button", { name: "Workspace menu" }));
  expect(
    await screen.findByRole("menuitem", { name: "Workspace settings" }),
  ).toBeTruthy();
  expect(
    (await screen.findByRole("menuitem", { name: "Design" })).getAttribute(
      "aria-current",
    ),
  ).toBe("true");
  expect(
    screen.queryByRole("menuitem", { name: "Create workspace" }),
  ).toBeNull();
});

it("searches the settings navigation without changing the selected page", async () => {
  access.organizationAdmin = true;
  const user = userEvent.setup();
  render(
    <MemoryRouter initialEntries={["/workspace/design/settings/general"]}>
      <Routes>
        <Route
          path="/workspace/:workspaceKey/settings/:section?"
          element={
            <SettingsLayout
              scope="workspace"
              content={{ general: <p>Workspace general</p> }}
            />
          }
        />
      </Routes>
    </MemoryRouter>,
  );
  const navigation = within(
    screen.getByRole("navigation", { name: "Settings navigation" }),
  );
  expect(navigation.getByRole("link", { name: "Models" })).toBeTruthy();
  await user.type(
    screen.getByRole("searchbox", { name: "Search settings" }),
    "password",
  );
  expect(screen.getByText("No matching settings")).toBeTruthy();
  expect(screen.getByText("Workspace general")).toBeTruthy();
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
    <MemoryRouter initialEntries={["/settings/profile"]}>
      <Routes>
        <Route
          path="/settings/:section?"
          element={
            <SettingsLayout
              scope="personal"
              content={{
                profile: <p>Profile form</p>,
                security: <p>Security form</p>,
              }}
            />
          }
        />
      </Routes>
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

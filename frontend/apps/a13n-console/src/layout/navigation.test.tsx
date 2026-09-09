// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { Shell } from "./shell";
import { SettingsLayout } from "../features/settings/layout";

vi.mock("../auth/context", () => ({
  useAuth: () => ({
    data: { user: { value: { name: "Alex", email: "alex@example.com" } } },
    logout: vi.fn(),
  }),
}));
vi.mock("./workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "workspace", name: "Design" },
    organization: { name: "Organization" },
    workspaces: [{ id: "workspace", name: "Design" }],
    organizationAdmin: false,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
afterEach(cleanup);

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
                  name="Design"
                  items={[
                    {
                      value: "profile",
                      label: "General",
                      content: <p>Workspace profile</p>,
                    },
                    {
                      value: "members",
                      label: "Members",
                      content: <p>Workspace members</p>,
                    },
                  ]}
                />
              }
            />
          </Route>
          <Route path="/" element={<Shell />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(
    screen.getByRole("complementary", { name: "Main navigation" }),
  ).toBeTruthy();
  await user.click(screen.getByRole("link", { name: "Workspace settings" }));
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

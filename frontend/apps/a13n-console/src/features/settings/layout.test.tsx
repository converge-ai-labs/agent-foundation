import userEvent from "@testing-library/user-event";
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { SettingsLayout } from "./layout";

vi.mock("../../layout/workspace", () => {
  const useAccess = () => ({
    basePath: "/workspace/ws_design",
    workspace: { id: "ws_design", name: "Design" },
    organization: { id: "organization", key: "acme", name: "Acme" },
    workspaces: [{ id: "ws_design", name: "Design" }],
    organizationCan: () => true,
    can: () => true,
  });
  return { useWorkspace: useAccess, useAccess };
});
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(cleanup);

function mount(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Routes>
        {(
          [
            ["/workspace/:workspaceId/settings/:section?", "workspace"],
            ["/settings/:section?", "personal"],
            ["/organization/settings/:section?", "organization"],
          ] as const
        ).map(([path, scope]) => (
          <Route
            key={path}
            path={path}
            element={
              <SettingsLayout
                scope={scope}
                content={{
                  general: <p>Workspace general</p>,
                  "app-plugins": <p>App management content</p>,
                  preferences: <p>Preferences content</p>,
                  members: <p>Workspace members</p>,
                  providers: <p>Workspace providers</p>,
                  webhooks: <p>Workspace webhooks</p>,
                }}
              />
            }
          />
        ))}
      </Routes>
    </MemoryRouter>,
  );
}

it("keeps the accessible scope order and active section when navigating between settings", async () => {
  mount("/workspace/ws_design/settings/members");
  const user = userEvent.setup();
  const navigation = within(
    screen.getByRole("navigation", { name: "Settings navigation" }),
  );
  const order = () =>
    navigation.getAllByRole("heading").map((heading) => heading.textContent);
  const scopeOrder = ["Personal", "Workspace", "Organization"];
  expect(order()).toEqual(scopeOrder);
  const workspaceMembers = navigation.getByRole("link", {
    name: "Members",
    current: "page",
  });
  expect(workspaceMembers.getAttribute("href")).toBe(
    "/workspace/ws_design/settings/members",
  );

  await user.click(navigation.getByRole("link", { name: "Preferences" }));
  expect(await screen.findByText("Preferences content")).toBeTruthy();
  expect(
    navigation.getByRole("link", { name: "Preferences", current: "page" }),
  ).toBeTruthy();
  expect(order()).toEqual(scopeOrder);

  const organizationMembers = navigation
    .getAllByRole("link", { name: "Members" })
    .find(
      (link) => link.getAttribute("href") === "/organization/settings/members",
    );
  expect(organizationMembers).toBeDefined();
  await user.click(organizationMembers!);
  expect(
    navigation.getByRole("link", { name: "Members", current: "page" }),
  ).toBe(organizationMembers);
  expect(order()).toEqual(scopeOrder);
  expect(navigation.queryByRole("link", { name: "Invitations" })).toBeNull();
});

it("keeps General as the organization settings landing page", () => {
  mount("/organization/settings");
  expect(screen.getByRole("heading", { name: "General" })).toBeTruthy();
  expect(screen.getByText("Workspace general")).toBeTruthy();
  expect(screen.queryByText("App management content")).toBeNull();
  expect(
    screen.getByRole("link", { name: "App plugins" }).getAttribute("href"),
  ).toBe("/organization/settings/app-plugins");
});

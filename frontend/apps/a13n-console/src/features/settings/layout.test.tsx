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

it("lists every scope the reader can reach without a disclosure", () => {
  mount("/workspace/ws_design/settings/members");
  const navigation = screen.getByRole("navigation", {
    name: "Settings navigation",
  });
  expect(navigation.querySelectorAll("button")).toHaveLength(0);
  for (const label of ["Workspace", "Personal", "Organization"])
    expect(
      screen.getByRole("heading", { name: new RegExp(label) }),
    ).toBeTruthy();
  const scopes = navigation.querySelectorAll("section");
  expect(Array.from(scopes, (item) => item.getAttribute("data-scope"))).toEqual(
    ["personal", "workspace", "organization"],
  );
  expect(
    within(scopes[1] as HTMLElement)
      .getByRole("link", { name: "Members" })
      .getAttribute("aria-current"),
  ).toBe("page");
  expect(
    screen.getByRole("link", { name: "Preferences" }).getAttribute("href"),
  ).toBe("/settings/preferences");
  expect(
    within(scopes[1] as HTMLElement)
      .getByRole("link", { name: "Webhooks" })
      .getAttribute("href"),
  ).toBe("/workspace/ws_design/settings/webhooks");
});

it("keeps scope positions fixed when navigating between settings", async () => {
  mount("/workspace/ws_design/settings/members");
  const user = userEvent.setup();
  const navigation = screen.getByRole("navigation", {
    name: "Settings navigation",
  });
  const order = () =>
    Array.from(navigation.querySelectorAll("section"), (item) =>
      item.getAttribute("data-scope"),
    );
  const initial = order();
  await user.click(screen.getByRole("link", { name: "Preferences" }));
  expect(order()).toEqual(initial);
  await user.click(
    within(
      navigation.querySelector('[data-scope="organization"]') as HTMLElement,
    ).getByRole("link", { name: "Members" }),
  );
  expect(order()).toEqual(initial);
  expect(
    within(navigation).queryByRole("link", { name: "Invitations" }),
  ).toBeNull();
});

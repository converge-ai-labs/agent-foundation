import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { SettingsLayout } from "./layout";

vi.mock("../../layout/workspace", () => {
  const useAccess = () => ({
    basePath: "/workspace/design",
    workspace: { id: "workspace", key: "design", name: "Design" },
    organization: { id: "organization", key: "acme", name: "Acme" },
    workspaces: [{ id: "workspace", key: "design", name: "Design" }],
    organizationCan: () => true,
    can: () => true,
  });
  return { useWorkspace: useAccess, useAccess };
});
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(cleanup);

function Location() {
  return (
    <output aria-label="Current address">{`${useLocation().pathname}${useLocation().search}`}</output>
  );
}

function mount(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <Location />
      <Routes>
        {["/workspace/:workspaceKey/settings/:section?"].map((path) => (
          <Route
            key={path}
            path={path}
            element={
              <SettingsLayout
                scope="workspace"
                content={{
                  general: <p>Workspace general</p>,
                  members: <p>Workspace members</p>,
                  providers: <p>Workspace providers</p>,
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
  mount("/workspace/design/settings/members");
  const navigation = screen.getByRole("navigation", {
    name: "Settings navigation",
  });
  expect(navigation.querySelectorAll("button")).toHaveLength(0);
  for (const label of ["Workspace", "Personal", "Organization"])
    expect(
      screen.getByRole("heading", { name: new RegExp(label) }),
    ).toBeTruthy();
  const scopes = navigation.querySelectorAll("section");
  expect(scopes[0].getAttribute("data-scope")).toBe("workspace");
  expect(
    within(scopes[0] as HTMLElement)
      .getByRole("link", { name: "Members" })
      .getAttribute("aria-current"),
  ).toBe("page");
  expect(
    screen.getByRole("link", { name: "Preferences" }).getAttribute("href"),
  ).toBe("/settings/preferences");
});

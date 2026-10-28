// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { AccountMenu } from "./account-menu";
import { WorkspaceMenu } from "./workspace-menu";

vi.mock("./avatar", () => ({
  UserAvatar: ({ name, url }: { name: string; url?: string | null }) => (
    <img alt={name} src={url ?? undefined} />
  ),
}));
vi.mock("../auth/context", () => ({
  useAuth: () => ({
    data: {
      user: {
        value: {
          name: "Ada",
          email: "ada@example.com",
          image_url: "/api/v1/users/usr_ada/avatar?v=1",
        },
      },
    },
    logout: vi.fn(),
  }),
}));
vi.mock("./workspace", () => {
  const design = {
    id: "ws_design",
    key: "design",
    name: "Design",
    image_url: "/api/v1/workspaces/ws_design/icon?v=2",
  };
  return {
    useAccess: () => ({ workspace: design }),
    useWorkspace: () => ({
      workspace: design,
      workspaces: [design],
      organization: { id: "org_acme", key: "acme", name: "Acme" },
      organizationCan: () => false,
      can: () => true,
    }),
  };
});
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(cleanup);

it("shows the account's avatar and the workspace's icon", () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <AccountMenu onNavigate={vi.fn()} />
        <WorkspaceMenu onNavigate={vi.fn()} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(screen.getByRole("img", { name: "Ada" }).getAttribute("src")).toBe(
    "/api/v1/users/usr_ada/avatar?v=1",
  );
  expect(screen.getByRole("img", { name: "Design" }).getAttribute("src")).toBe(
    "/api/v1/workspaces/ws_design/icon?v=2",
  );
});

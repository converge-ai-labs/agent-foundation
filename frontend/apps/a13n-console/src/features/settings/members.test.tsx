// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError } from "../../service-client";
import { MemoryRouter } from "react-router";
import { Members, MemberSettings } from "./members";

const access = vi.hoisted(() => ({ organizationAdmin: true }));
const http = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  PATCH: vi.fn(),
  DELETE: vi.fn(),
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http, workspace: () => http }),
}));
vi.mock("../../layout/workspace", () => ({
  useAccess: () => ({
    organization: { id: "org_acme" },
    organizationCan: () => access.organizationAdmin,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) =>
      options?.defaultValue ?? key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  access.organizationAdmin = true;
});

const scope = { kind: "workspace", id: "ws_design" } as const;
function grant(
  id: string,
  role: string,
  principal: { id: string; kind: string; name: string },
) {
  return {
    id,
    role,
    organization_id: "org_acme",
    workspace_id: scope.id,
    created_by_id: "usr_admin",
    created_at: "2026-09-01T00:00:00Z",
    principal: { email: null, image_url: null, status: "active", ...principal },
  };
}
const ada = { id: "usr_ada", kind: "user", name: "Ada" };

function setup(items: () => ReturnType<typeof grant>[]) {
  http.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/members")
      ? { items: [{ ...ada, email: "ada@example.com" }], next_cursor: null }
      : { items: items(), next_cursor: null },
    response: new Response(),
  }));
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <Members scope={scope} />
    </QueryClientProvider>,
  );
  return userEvent.setup();
}

it("changes a role from the member's current grant after someone replaced it", async () => {
  let current = grant("rb_old", "viewer", ada);
  const user = setup(() => [current]);
  http.PATCH.mockImplementation(
    async (_path: string, init: { params: { path: { grant_id: string } } }) => {
      if (init.params.path.grant_id === "rb_old")
        throw new ApiError(
          404,
          "not_found",
          "grant rb_old not found",
          {},
          null,
        );
      return { data: current, response: new Response() };
    },
  );

  await user.click(
    await screen.findByRole("button", { name: "Member actions" }),
  );
  await user.click(
    await screen.findByRole("menuitem", { name: "Change role" }),
  );
  // Someone else changes the role while the dialog is open.
  current = grant("rb_new", "runner", ada);
  await user.click(screen.getByRole("combobox", { name: "Role" }));
  await user.click(await screen.findByRole("option", { name: "builder" }));
  await user.click(screen.getByRole("button", { name: "Save changes" }));

  await screen.findByText("This member changed");
  await user.click(screen.getByRole("button", { name: "Load current role" }));
  await waitFor(() =>
    expect(
      screen.getByRole("combobox", { name: "Role" }).textContent,
    ).toContain("runner"),
  );
  await user.click(screen.getByRole("combobox", { name: "Role" }));
  await user.click(await screen.findByRole("option", { name: "builder" }));
  await user.click(screen.getByRole("button", { name: "Save changes" }));

  await waitFor(() => expect(http.PATCH).toHaveBeenCalledTimes(2));
  expect(http.PATCH).toHaveBeenLastCalledWith(
    "/api/v1/workspaces/{workspace_id}/grants/{grant_id}",
    {
      params: { path: { workspace_id: scope.id, grant_id: "rb_new" } },
      body: { role: "builder" },
    },
  );
  await waitFor(() =>
    expect(screen.queryByText("This member changed")).toBeNull(),
  );
});

it("warns that removing a service account's role disables it", async () => {
  const user = setup(() => [
    grant("rb_bot", "runner", {
      id: "sa_bot",
      kind: "service_account",
      name: "Deploy bot",
    }),
  ]);
  http.DELETE.mockResolvedValue({ response: new Response(null) });

  await user.click(
    await screen.findByRole("button", { name: "Member actions" }),
  );
  await user.click(await screen.findByRole("menuitem", { name: "Remove" }));
  await screen.findByText(
    "This removes the service account's only role, which disables it and revokes its keys.",
  );
  await user.click(screen.getByRole("button", { name: "Remove member" }));
  await waitFor(() =>
    expect(http.DELETE).toHaveBeenCalledWith(
      "/api/v1/workspaces/{workspace_id}/grants/{grant_id}",
      { params: { path: { workspace_id: scope.id, grant_id: "rb_bot" } } },
    ),
  );
});

it("offers the organization's users when adding a workspace member", async () => {
  const user = setup(() => []);
  await user.click(
    (await screen.findAllByRole("button", { name: "Add member" }))[0]!,
  );
  await waitFor(() =>
    expect(http.GET).toHaveBeenCalledWith(
      "/api/v1/organizations/{organization_id}/members",
      expect.objectContaining({
        params: {
          path: { organization_id: "org_acme" },
          query: { kind: "user", cursor: undefined, limit: 100 },
        },
      }),
    ),
  );
});

it.each(["workspace", "organization"] as const)(
  "invites directly from %s Members and opens invitation records",
  async (kind) => {
    const target = {
      kind,
      id: kind === "workspace" ? "ws_design" : "org_acme",
    };
    http.GET.mockImplementation(async (path: string) => ({
      data: {
        items: path.endsWith("/invitations")
          ? [
              {
                id: "inv_ada",
                email: "ada@example.com",
                role: "viewer",
                created_at: "2026-10-20T00:00:00Z",
                expires_at: "2099-10-27T00:00:00Z",
                accepted_at: null,
                revoked_at: null,
              },
            ]
          : [grant("rb_ada", "viewer", ada)],
        next_cursor: null,
      },
      response: new Response(),
    }));
    http.POST.mockResolvedValue({
      data: { delivery: "sent" },
      response: new Response(),
    });
    render(
      <MemoryRouter>
        <QueryClientProvider
          client={
            new QueryClient({ defaultOptions: { queries: { retry: false } } })
          }
        >
          <MemberSettings scope={target} />
        </QueryClientProvider>
      </MemoryRouter>,
    );
    const user = userEvent.setup();
    await screen.findByText("Ada");
    await user.click(screen.getByRole("button", { name: "Invite" }));
    await user.type(
      screen.getByRole("textbox", { name: "Email address" }),
      "ada@example.com",
    );
    await user.click(screen.getByRole("button", { name: "Send invitation" }));
    await screen.findByText("Invitation sent");
    expect(http.POST).toHaveBeenCalledWith(
      `/api/v1/${kind}s/{${kind}_id}/invitations`,
      {
        params: { path: { [`${kind}_id`]: target.id } },
        body: { email: "ada@example.com", role: "viewer" },
      },
    );
    await user.click(screen.getByRole("button", { name: "Done" }));
    await user.click(screen.getByRole("tab", { name: "Invitations" }));
    await screen.findByText("ada@example.com");
    expect(
      screen
        .getByRole("tab", { name: "Invitations" })
        .getAttribute("aria-selected"),
    ).toBe("true");
    expect(http.GET).toHaveBeenCalledWith(
      `/api/v1/${kind}s/{${kind}_id}/invitations`,
      expect.objectContaining({
        params: {
          path: { [`${kind}_id`]: target.id },
          query: { cursor: undefined, limit: 30 },
        },
      }),
    );
  },
);

it("lets workspace administrators invite without organization administration", async () => {
  access.organizationAdmin = false;
  const user = setup(() => [grant("rb_ada", "admin", ada)]);
  await screen.findByText("Ada");
  expect(screen.queryByRole("button", { name: "Add member" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Invite" }));
  expect(screen.getByRole("dialog", { name: "Invite member" })).toBeTruthy();
});

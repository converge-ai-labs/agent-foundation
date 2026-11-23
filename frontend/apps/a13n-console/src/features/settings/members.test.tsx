// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError } from "../../service-client";
import { MemoryRouter } from "react-router";
import { MemberSettings } from "./members";

const access = vi.hoisted(() => ({
  organizationAdmin: true,
  workspaceAdmin: true,
  archived: false,
}));
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
    workspace: { archived_at: access.archived ? "2026-09-01T00:00:00Z" : null },
    can: () => access.workspaceAdmin,
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
  access.workspaceAdmin = true;
  access.archived = false;
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
    data: path.endsWith("/configuration")
      ? { password_enabled: true }
      : path.endsWith("/members")
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
      <MemoryRouter>
        <MemberSettings scope={scope} />
      </MemoryRouter>
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
      data: path.endsWith("/configuration")
        ? { password_enabled: true }
        : {
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
    await user.click(await screen.findByRole("button", { name: "Invite" }));
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

it("lets workspace administrators grant by principal ID without listing the organization directory", async () => {
  access.organizationAdmin = false;
  const user = setup(() => [grant("rb_ada", "admin", ada)]);
  await screen.findByText("Ada");
  await user.click(screen.getByRole("button", { name: "Add member" }));
  const dialog = screen.getByRole("dialog", { name: "Add existing member" });
  http.POST.mockResolvedValue({
    response: new Response(null, { status: 201 }),
  });
  await user.type(
    within(dialog).getByRole("textbox", { name: "Principal ID" }),
    "usr_grace",
  );
  await user.click(within(dialog).getByRole("button", { name: "Add member" }));
  expect(http.POST).toHaveBeenCalledWith(
    "/api/v1/workspaces/{workspace_id}/grants",
    {
      params: { path: { workspace_id: scope.id } },
      body: { principal_id: "usr_grace", role: "viewer" },
    },
  );
  expect(http.GET.mock.calls.some(([path]) => path.endsWith("/members"))).toBe(
    false,
  );
});

it.each([undefined, true])(
  "renders invitation actions from the known policy (%s) until disabled",
  async (cached) => {
    let complete: (value: unknown) => void = () => {};
    const configuration = new Promise((resolve) => {
      complete = resolve;
    });
    http.GET.mockImplementation(async (path: string) => {
      const data = path.endsWith("/configuration")
        ? await configuration
        : {
            items: [
              {
                id: "inv_old",
                email: "old@example.com",
                role: "viewer",
                created_at: "2026-10-20T00:00:00Z",
                expires_at: "2099-10-27T00:00:00Z",
                accepted_at: null,
                revoked_at: null,
              },
            ],
            next_cursor: null,
          };
      return { data, response: new Response() };
    });
    const cache = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    if (cached)
      cache.setQueryData(["auth-configuration"], { password_enabled: true });
    render(
      <QueryClientProvider client={cache}>
        <MemoryRouter initialEntries={["/?tab=invitations"]}>
          <MemberSettings scope={{ kind: "organization", id: "org_acme" }} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const user = userEvent.setup();
    await screen.findByText("old@example.com");
    expect(Boolean(screen.queryByRole("button", { name: "Invite" }))).toBe(
      cached === true,
    );
    await user.click(
      screen.getByRole("button", { name: "Invitation actions" }),
    );
    if (cached) await screen.findByText("Resend", { exact: true });
    else expect(screen.queryByText("Resend", { exact: true })).toBeNull();
    complete({ password_enabled: false });
    await waitFor(() =>
      expect(cache.getQueryData(["auth-configuration"])).toEqual({
        password_enabled: false,
      }),
    );
    expect(screen.queryByRole("button", { name: "Invite" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reset password" })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: "Resend" })).toBeNull();
    expect(screen.getByRole("menuitem", { name: "Revoke" })).toBeTruthy();
  },
);

it("edits the effective organization role and only offers SSO defaults for OIDC users", async () => {
  const grace = { id: "usr_grace", kind: "user", name: "Grace" };
  const items = [
    {
      ...grant("rb_sso_ada", "admin", ada),
      workspace_id: null,
      source: "oidc",
    },
    {
      ...grant("rb_grace", "viewer", grace),
      workspace_id: null,
      source: "manual",
    },
  ];
  http.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/configuration")
      ? { password_enabled: false }
      : { items, next_cursor: null },
    response: new Response(),
  }));
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter>
        <MemberSettings scope={{ kind: "organization", id: "org_acme" }} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  const sso = await screen.findByRole("row", { name: /Ada.*SSO/ });
  await user.click(within(sso).getByRole("button", { name: "Member actions" }));
  const edit = await screen.findByRole("menuitem", { name: "Change role" });
  expect(screen.queryByRole("menuitem", { name: "Remove" })).toBeNull();
  await user.click(edit);
  const editor = await screen.findByRole("dialog", { name: "Change role" });
  expect(
    within(editor).getByRole("combobox", { name: "Role source" }).textContent,
  ).toContain("Use SSO default");
  expect(within(editor).queryByRole("combobox", { name: "Role" })).toBeNull();
  await user.click(within(editor).getByRole("button", { name: "Cancel" }));
  const manual = screen.getByRole("row", { name: /Grace.*viewer/i });
  await user.click(
    within(manual).getByRole("button", { name: "Member actions" }),
  );
  await user.click(
    await screen.findByRole("menuitem", { name: "Change role" }),
  );
  const passwordEditor = await screen.findByRole("dialog", {
    name: "Change role",
  });
  expect(
    within(passwordEditor).queryByRole("combobox", { name: "Role source" }),
  ).toBeNull();
  expect(
    within(passwordEditor).getByRole("combobox", { name: "Role" }),
  ).toBeTruthy();
});

it("keeps only ordinary manual removal available in an archived workspace", async () => {
  access.archived = true;
  const user = setup(() => [
    { ...grant("oidcrb_ada", "admin", ada), source: "oidc" },
    {
      ...grant("rb_grace", "viewer", {
        id: "usr_grace",
        kind: "user",
        name: "Grace",
      }),
      source: "manual",
      overrides_sso: true,
    },
    grant("rb_lee", "runner", { id: "usr_lee", kind: "user", name: "Lee" }),
  ]);
  const sso = await screen.findByRole("row", { name: /Ada.*SSO/ });
  const override = screen.getByRole("row", { name: /Grace.*Manual/ });
  for (const row of [sso, override]) {
    expect(
      within(row).queryByRole("button", { name: "Member actions" }),
    ).toBeNull();
  }
  const manual = screen.getByRole("row", { name: /Lee.*runner/ });
  await user.click(
    within(manual).getByRole("button", { name: "Member actions" }),
  );
  expect(await screen.findByRole("menuitem", { name: "Remove" })).toBeTruthy();
  expect(screen.queryByRole("menuitem", { name: "Change role" })).toBeNull();
});

it.each([false, true])(
  "lets an organization admin copy a reset link (SMTP: %s)",
  async (emailDelivery) => {
    http.GET.mockImplementation(async (path: string) => ({
      data: path.endsWith("/configuration")
        ? {
            password_enabled: true,
            manual_password_reset: true,
            email_delivery: emailDelivery,
          }
        : {
            items: path.endsWith("/members")
              ? [
                  { ...ada, email: "ada@example.com", status: "active" },
                  {
                    ...ada,
                    id: "usr_sso",
                    name: "SSO Member",
                    email: null,
                    oidc_email: "sso@example.com",
                    status: "active",
                  },
                ]
              : [],
            next_cursor: null,
          },
      response: new Response(),
    }));
    http.POST.mockResolvedValue({
      data: { reset_url: "https://service.test/reset-password#token=fixture" },
      response: new Response(),
    });
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
          <MemberSettings scope={{ kind: "organization", id: "org_acme" }} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const user = userEvent.setup();
    await user.click(
      await screen.findByRole("button", { name: "Reset password" }),
    );
    await user.click(screen.getByRole("combobox", { name: "Member" }));
    const local = await screen.findByRole("option", {
      name: /Ada.*ada@example.com/,
    });
    expect(screen.queryByRole("option", { name: /SSO Member/ })).toBeNull();
    await user.click(local);
    expect(
      Boolean(screen.queryByRole("button", { name: "Send reset email" })),
    ).toBe(emailDelivery);
    await user.click(
      screen.getByRole("button", { name: "Generate reset link" }),
    );
    expect(http.POST).toHaveBeenCalledWith(
      "/api/v1/organizations/{organization_id}/members/{user_id}/password-reset",
      {
        params: { path: { organization_id: "org_acme", user_id: ada.id } },
        body: { delivery: "manual" },
      },
    );
    await user.click(
      await screen.findByRole("button", { name: "Copy reset link" }),
    );
    await user.click(screen.getByRole("button", { name: "Done" }));
    await user.click(screen.getByRole("button", { name: "Reset password" }));
    expect(
      screen.queryByRole("button", { name: "Copy reset link" }),
    ).toBeNull();
  },
);

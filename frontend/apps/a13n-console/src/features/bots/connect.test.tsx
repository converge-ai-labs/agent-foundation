import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { BotConnect } from "./connect";

const state = vi.hoisted(() => ({
  admin: true,
  http: { GET: vi.fn(), POST: vi.fn() },
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: state.http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspace/test",
    can: () => state.admin,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
const account = {
  id: "acct_test",
  workspace_id: "ws_test",
  name: "Pilot",
  provider_key: "slack",
  provider_config_version: "slack_http_v1",
  version: 1,
  credential_generation: 1,
  receive_enabled: false,
  reception_scope: "configured_targets",
  provider_config: {},
};
const response = (data: unknown) => ({
  data,
  response: new Response(null, { status: 200 }),
});
const schema = (properties: Record<string, unknown>) => ({
  type: "object",
  properties,
  required: Object.keys(properties),
});
const string = (title: string) => ({ type: "string", title, minLength: 1 });
const definitions = {
  items: [
    {
      provider_key: "slack",
      config_version: "slack_http_v1",
      configuration_schema: schema({ team_id: string("Workspace ID") }),
      credential_schema: schema({
        bot_token: string("Bot token"),
        signing_secret: string("Signing secret"),
      }),
      reception_policy_schema: schema({}),
      target_kinds: ["conversation"],
    },
    {
      provider_key: "lark",
      config_version: "lark_http_v1",
      configuration_schema: schema({
        brand: string("Brand"),
        open_api_origin: string("Origin"),
        app_id: string("App ID"),
        tenant_key: string("Tenant Key"),
        bot_open_id: string("Bot Open Id"),
      }),
      credential_schema: schema({
        app_secret: string("App Secret"),
        verification_token: string("Verification Token"),
      }),
      reception_policy_schema: schema({}),
      target_kinds: ["conversation"],
    },
  ],
};
function setup(path = "/workspace/test/bots/connect") {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false, gcTime: 0 },
            mutations: { retry: false },
          },
        })
      }
    >
      <MemoryRouter initialEntries={[path]}>
        <BotConnect />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.resetAllMocks();
  state.admin = true;
  state.http.GET.mockImplementation(async (path: string) => {
    if (path.endsWith("application-account-provider-types"))
      return response(definitions);
    if (path.endsWith("/checks/latest")) return response({ latest: null });
    if (path.endsWith("/bot/setup"))
      return response({
        event_path: "/connectivity/v1/accounts/acct_test/events",
        event_url:
          "https://events.example.test/connectivity/v1/accounts/acct_test/events",
      });
    if (path.endsWith("/{account_id}")) return response(account);
    return response({ items: [], next_cursor: null });
  });
});
afterEach(cleanup);

it("does not fetch accounts or credentials for a viewer", () => {
  state.admin = false;
  setup();
  expect(
    screen.getByText("Only workspace administrators can connect bots."),
  ).toBeTruthy();
  expect(state.http.GET).not.toHaveBeenCalled();
});

it("chooses a platform before accounts and filters reuse without creating duplicates", async () => {
  const original = state.http.GET.getMockImplementation()!;
  state.http.GET.mockImplementation(
    async (
      path: string,
      options: { params?: { query?: { platform?: string } } },
    ) => {
      if (path.endsWith("/bots"))
        return response({
          items:
            options.params?.query?.platform === "slack"
              ? [
                  {
                    account: {
                      ...account,
                      provider_config: { team_id: "T_EXISTING" },
                    },
                  },
                ]
              : [
                  {
                    account: {
                      ...account,
                      id: "acct_feishu",
                      name: "Feishu pilot",
                      provider_key: "lark",
                    },
                  },
                ],
          next_cursor: null,
        });
      return original(path, options);
    },
  );
  setup();
  expect(
    screen.getByRole("heading", { name: "Choose your platform" }),
  ).toBeTruthy();
  expect(
    screen.queryByRole("tab", { name: "Use an existing account" }),
  ).toBeNull();
  expect(state.http.GET).not.toHaveBeenCalled();
  await userEvent.click(
    screen.getByRole("button", { name: new RegExp("^Slack") }),
  );
  expect(
    (await screen.findByRole("link", { name: /Pilot/ })).getAttribute("href"),
  ).toBe("/workspace/test/bots/acct_test");
  expect(state.http.GET).toHaveBeenCalledWith(
    "/api/v1/workspaces/{workspace}/bots",
    expect.objectContaining({
      params: {
        path: { workspace: "ws_test" },
        query: { limit: 20, platform: "slack" },
      },
    }),
  );
  expect(screen.getByText("Slack · T_EXISTING")).toBeTruthy();
  await userEvent.click(
    screen.getByRole("tab", { name: "Create a new account" }),
  );
  await screen.findByLabelText("Bot token");
  await userEvent.type(screen.getByLabelText("Bot token"), "unsaved-secret");
  await userEvent.click(
    screen.getByRole("button", { name: "Change platform" }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: new RegExp("^Feishu") }),
  );
  await screen.findByRole("link", { name: /Feishu pilot/ });
  expect(screen.queryByRole("link", { name: /^Pilot/ })).toBeNull();
  expect(screen.queryByLabelText("Bot token")).toBeNull();
  expect(
    screen
      .getByRole("tab", { name: "Use an existing account" })
      .getAttribute("aria-selected"),
  ).toBe("true");
  await userEvent.click(
    screen.getByRole("tab", { name: "Create a new account" }),
  );
  await screen.findByLabelText("App secret");
  expect(screen.queryByLabelText("Bot token")).toBeNull();
  expect(state.http.POST).not.toHaveBeenCalled();
});

it("opens creation for an empty platform and keeps the platform when cancelled", async () => {
  setup();
  await userEvent.click(
    screen.getByRole("button", { name: new RegExp("^Slack") }),
  );
  await screen.findByLabelText("Bot token");
  expect(
    screen
      .getByRole("tab", { name: "Create a new account" })
      .getAttribute("aria-selected"),
  ).toBe("true");
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.getByRole("button", { name: "Change platform" })).toBeTruthy();
  await userEvent.click(
    await screen.findByRole("button", { name: "Create a new account" }),
  );
  await screen.findByLabelText("Bot token");
});

it("shows account lookup failures instead of assuming no accounts exist", async () => {
  state.http.GET.mockRejectedValue(new Error("Account lookup unavailable"));
  setup();
  await userEvent.click(
    screen.getByRole("button", { name: new RegExp("^Slack") }),
  );
  await screen.findByText("Account lookup unavailable");
  expect(screen.queryByLabelText("Bot token")).toBeNull();
  expect(state.http.POST).not.toHaveBeenCalled();
});

it("saves a new pilot with reception off and clears credentials before verification", async () => {
  state.http.POST.mockResolvedValue(response(account));
  setup();
  await userEvent.click(
    screen.getByRole("button", { name: new RegExp("^Slack") }),
  );
  await userEvent.type(
    await screen.findByRole("textbox", { name: "Name" }),
    "Pilot",
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "Workspace ID" }),
    "T1",
  );
  await userEvent.type(screen.getByLabelText("Bot token"), "fictional-token");
  await userEvent.type(
    screen.getByLabelText("Signing secret"),
    "fictional-secret",
  );
  expect(screen.queryByText("Receive events")).toBeNull();
  await userEvent.click(
    screen.getByRole("button", { name: "Save and verify" }),
  );
  await screen.findByText("Event endpoint");
  expect(screen.queryByLabelText("Bot token")).toBeNull();
  expect(state.http.POST).toHaveBeenCalledWith(
    "/api/v1/workspaces/{workspace}/application-accounts",
    expect.objectContaining({
      body: expect.objectContaining({
        receive_enabled: false,
        reception_scope: "configured_targets",
        credentials: {
          bot_token: "fictional-token",
          signing_secret: "fictional-secret",
        },
      }),
    }),
  );
  expect(
    (
      screen.getByRole("button", {
        name: "Continue",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(
    (
      (await screen.findByRole("textbox", {
        name: "Event endpoint",
      })) as HTMLInputElement
    ).value,
  ).toBe(
    "https://events.example.test/connectivity/v1/accounts/acct_test/events",
  );
});

it("reconciles a lost save using the same command and rejects changed credentials", async () => {
  state.http.POST.mockRejectedValueOnce(
    new TypeError("Network error"),
  ).mockResolvedValue(response(account));
  setup();
  await userEvent.click(
    screen.getByRole("button", { name: new RegExp("^Slack") }),
  );
  await userEvent.type(
    await screen.findByRole("textbox", { name: "Name" }),
    "Pilot",
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "Workspace ID" }),
    "T1",
  );
  await userEvent.type(screen.getByLabelText("Bot token"), "fictional-token");
  await userEvent.type(
    screen.getByLabelText("Signing secret"),
    "fictional-secret",
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Save and verify" }),
  );
  await waitFor(() =>
    expect((screen.getByLabelText("Bot token") as HTMLInputElement).value).toBe(
      "",
    ),
  );
  await userEvent.type(screen.getByLabelText("Bot token"), "different-token");
  await userEvent.type(
    screen.getByLabelText("Signing secret"),
    "fictional-secret",
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Save and verify" }),
  );
  await screen.findByText(
    "The previous save is unconfirmed. Re-enter the same credentials and retry the unchanged setup to recover its result.",
  );
  expect(state.http.POST).toHaveBeenCalledTimes(1);
  // Re-entering fields in another order still recovers the same command.
  await userEvent.type(
    screen.getByLabelText("Signing secret"),
    "fictional-secret",
  );
  await userEvent.type(screen.getByLabelText("Bot token"), "fictional-token");
  await userEvent.click(
    screen.getByRole("button", { name: "Save and verify" }),
  );
  await screen.findByText("Event endpoint");
  expect(state.http.POST).toHaveBeenCalledTimes(2);
  expect(state.http.POST.mock.calls[1][1].params.header).toEqual(
    state.http.POST.mock.calls[0][1].params.header,
  );
});

it("resumes a saved account without creating another account or claiming a test passed", async () => {
  setup("/workspace/test/bots/connect?account=acct_test&step=test");
  await screen.findByText("Event endpoint");
  expect(screen.queryByLabelText("Bot token")).toBeNull();
  expect(screen.queryByText("Test in your pilot conversation")).toBeNull();
  expect(state.http.POST).not.toHaveBeenCalled();
});

it("does not reuse an installation check from an older credential generation", async () => {
  const original = state.http.GET.getMockImplementation()!;
  state.http.GET.mockImplementation(async (path: string, options: unknown) =>
    path.endsWith("/checks/latest")
      ? response({
          latest: {
            credential_generation: 0,
            installation: { enabled: true },
            error_code: null,
          },
        })
      : original(path, options),
  );
  setup("/workspace/test/bots/connect?account=acct_test&step=reception");
  await screen.findByText("Event endpoint");
  expect(
    (
      screen.getByRole("button", {
        name: "Continue",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(screen.queryByText("Choose a pilot conversation")).toBeNull();
});

const feishuIdentity = {
  app_id: "cli_test",
  organization_id: "tenant_verified",
  organization_name: "Test enterprise",
  bot_id: "ou_verified",
  bot_name: "Test bot",
  enabled: true,
};
async function fillFeishuCredentials() {
  await userEvent.type(
    screen.getByLabelText("App secret"),
    "fictional-app-secret",
  );
  await userEvent.type(
    screen.getByLabelText("Verification token"),
    "fictional-verification",
  );
}
async function startFeishu() {
  setup();
  await userEvent.click(
    screen.getByRole("button", { name: new RegExp("^Feishu") }),
  );
  await userEvent.type(
    await screen.findByRole("textbox", { name: "Name" }),
    "Pilot",
  );
  await userEvent.type(
    screen.getByRole("textbox", { name: "App ID" }),
    "cli_test",
  );
  expect(screen.queryByLabelText("Tenant key")).toBeNull();
  expect(screen.queryByLabelText("Bot open ID")).toBeNull();
  await fillFeishuCredentials();
}
it("discovers Feishu identity before saving without asking for installation IDs", async () => {
  state.http.POST.mockImplementation(async (path: string) =>
    response(path.endsWith("/feishu/installation") ? feishuIdentity : account),
  );
  await startFeishu();
  await userEvent.click(
    screen.getByRole("button", { name: "Save and verify" }),
  );
  await screen.findByText("Event endpoint");
  expect(state.http.POST).toHaveBeenNthCalledWith(
    1,
    "/api/v1/workspaces/{workspace}/bots/feishu/installation",
    expect.objectContaining({
      body: { app_id: "cli_test", app_secret: "fictional-app-secret" },
    }),
  );
  expect(state.http.POST).toHaveBeenNthCalledWith(
    2,
    "/api/v1/workspaces/{workspace}/application-accounts",
    expect.objectContaining({
      body: expect.objectContaining({
        provider_config: {
          event_transport: "http",
          brand: "feishu",
          open_api_origin: "https://open.feishu.cn",
          app_id: "cli_test",
          tenant_key: "tenant_verified",
          bot_open_id: "ou_verified",
        },
        receive_enabled: false,
      }),
    }),
  );
});
it("does not create an account when Feishu discovery fails", async () => {
  state.http.POST.mockRejectedValue(
    new Error("Feishu app credentials rejected"),
  );
  await startFeishu();
  await userEvent.click(
    screen.getByRole("button", { name: "Save and verify" }),
  );
  await screen.findByText("Feishu app credentials rejected");
  expect(state.http.POST).toHaveBeenCalledTimes(1);
  expect((screen.getByLabelText("App secret") as HTMLInputElement).value).toBe(
    "",
  );
});
it("reuses discovered identity and the creation command after an uncertain Feishu save", async () => {
  state.http.POST.mockResolvedValueOnce(response(feishuIdentity))
    .mockRejectedValueOnce(new TypeError("Lost save response"))
    .mockResolvedValue(response(account));
  await startFeishu();
  await userEvent.click(
    screen.getByRole("button", { name: "Save and verify" }),
  );
  await screen.findByText("Lost save response");
  await fillFeishuCredentials();
  await userEvent.click(
    screen.getByRole("button", { name: "Save and verify" }),
  );
  await screen.findByText("Event endpoint");
  expect(state.http.POST).toHaveBeenCalledTimes(3);
  expect(state.http.POST.mock.calls[2]).toEqual(state.http.POST.mock.calls[1]);
});

it("creates a Feishu long connection with app credentials only", async () => {
  state.http.POST.mockImplementation(async (path: string) =>
    response(
      path.endsWith("/feishu/installation")
        ? feishuIdentity
        : {
            ...account,
            provider_key: "lark",
            provider_config: { event_transport: "websocket" },
          },
    ),
  );
  await startFeishu();
  await userEvent.click(
    screen.getByRole("combobox", { name: "Event connection" }),
  );
  await userEvent.click(
    await screen.findByRole("option", { name: "Long connection (WebSocket)" }),
  );
  expect(screen.queryByLabelText("Verification token")).toBeNull();
  await userEvent.type(screen.getByLabelText("App secret"), "socket-secret");
  await userEvent.click(
    screen.getByRole("button", { name: "Save and verify" }),
  );
  await waitFor(() =>
    expect(state.http.POST).toHaveBeenCalledWith(
      "/api/v1/workspaces/{workspace}/application-accounts",
      expect.objectContaining({
        body: expect.objectContaining({
          provider_config: expect.objectContaining({
            event_transport: "websocket",
            tenant_key: "tenant_verified",
          }),
          credentials: { app_secret: "socket-secret" },
        }),
      }),
    ),
  );
});

it("shows live socket status without an HTTP callback instruction", async () => {
  const original = state.http.GET.getMockImplementation()!;
  state.http.GET.mockImplementation(async (path: string, options: unknown) =>
    path.endsWith("/{account_id}")
      ? response({
          ...account,
          provider_config: { event_transport: "websocket" },
        })
      : path.endsWith("/event-connection")
        ? response({ transport: "websocket", state: "connected" })
        : original(path, options),
  );
  setup("/workspace/test/bots/connect?account=acct_test");
  await screen.findByText("Connected");
  expect(screen.queryByText("Event endpoint")).toBeNull();
  expect(
    screen.getByText(
      "Connected confirms the event connection only. Use the setup test to verify message reception, agent execution, and replies.",
    ),
  ).toBeTruthy();
});

it("connects a GitHub polling account without a public callback or claimed user ID", async () => {
  const github = {
    ...account,
    provider_key: "github",
    provider_config_version: "github_notifications_v1",
    provider_config: { user_id: 99 },
  };
  state.http.GET.mockImplementation(async (path: string) => {
    if (path.endsWith("application-account-provider-types"))
      return response({
        items: [
          {
            provider_key: "github",
            config_version: "github_notifications_v1",
            configuration_schema: schema({
              user_id: { type: "integer", title: "User ID" },
              api_origin: string("API origin"),
              web_origin: string("Web origin"),
            }),
            credential_schema: schema({
              personal_access_token: string("Personal access token"),
            }),
            reception_policy_schema: schema({}),
            target_kinds: ["repository"],
          },
        ],
      });
    if (path.endsWith("/{account_id}")) return response(github);
    if (path.endsWith("/checks/latest")) return response({ latest: null });
    if (path.endsWith("/bot/setup"))
      return response({
        reception_mode: "polling",
        event_path: null,
        event_url: null,
      });
    return response({ items: [], next_cursor: null });
  });
  state.http.POST.mockImplementation(async (path: string) =>
    path.endsWith("/github/user")
      ? response({ bot_id: "99", bot_name: "helper" })
      : response(github),
  );
  setup();
  await userEvent.click(
    screen.getByRole("button", { name: new RegExp("^GitHub") }),
  );
  await userEvent.type(
    await screen.findByRole("textbox", { name: "Name" }),
    "GitHub helper",
  );
  expect(screen.queryByLabelText("User ID")).toBeNull();
  expect(screen.queryByLabelText("API origin")).toBeNull();
  await userEvent.type(
    screen.getByLabelText("Personal access token"),
    "fictional-pat",
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Save and verify" }),
  );
  expect(await screen.findByText("Notification polling")).toBeTruthy();
  expect(screen.queryByText("Event endpoint")).toBeNull();
  expect(screen.queryByLabelText("Personal access token")).toBeNull();
  expect(state.http.POST).toHaveBeenCalledWith(
    "/api/v1/workspaces/{workspace}/application-accounts",
    expect.objectContaining({
      body: expect.objectContaining({
        provider_config: {
          api_origin: "https://api.github.com",
          web_origin: "https://github.com",
          user_id: 99,
        },
        provider_config_version: "github_notifications_v1",
        reception_scope: "configured_targets",
        receive_enabled: false,
      }),
    }),
  );
});

const githubDefinitions = [
  {
    provider_key: "github",
    config_version: "github_notifications_v1",
    configuration_schema: schema({
      user_id: { type: "integer", title: "User ID" },
      api_origin: string("API origin"),
      web_origin: string("Web origin"),
    }),
    credential_schema: schema({
      personal_access_token: string("Personal access token"),
    }),
    reception_policy_schema: schema({}),
    target_kinds: ["repository"],
  },
  {
    provider_key: "github",
    config_version: "github_app_http_v1",
    configuration_schema: schema({
      app_id: { type: "integer", title: "App ID" },
    }),
    credential_schema: schema({
      app_private_key_pem: string("App private key"),
      webhook_secret: string("Webhook secret"),
    }),
    reception_policy_schema: schema({}),
    target_kinds: ["repository"],
  },
];
it("reuses both GitHub account types under one filtered platform without creating an account", async () => {
  state.http.GET.mockResolvedValue(
    response({
      items: [
        {
          account: {
            ...account,
            id: "acct_pat",
            name: "Repository helper",
            provider_key: "github",
            provider_config_version: "github_notifications_v1",
          },
        },
        {
          account: {
            ...account,
            id: "acct_app",
            name: "Review app",
            provider_key: "github",
            provider_config_version: "github_app_http_v1",
          },
        },
      ],
      next_cursor: null,
    }),
  );
  setup();
  expect(state.http.GET).not.toHaveBeenCalled();
  await userEvent.click(
    screen.getByRole("button", { name: new RegExp("^GitHub") }),
  );
  expect(
    (
      await screen.findByRole("link", { name: /Repository helper/ })
    ).getAttribute("href"),
  ).toBe("/workspace/test/bots/acct_pat");
  expect(
    screen.getByRole("link", { name: /Review app/ }).getAttribute("href"),
  ).toBe("/workspace/test/bots/acct_app");
  expect(screen.getByText("GitHub account · Polling")).toBeTruthy();
  expect(screen.getByText("GitHub App · Webhook")).toBeTruthy();
  expect(state.http.GET).toHaveBeenCalledWith(
    "/api/v1/workspaces/{workspace}/bots",
    expect.objectContaining({
      params: {
        path: { workspace: "ws_test" },
        query: { limit: 20, platform: "github" },
      },
    }),
  );
  expect(state.http.POST).not.toHaveBeenCalled();
});
it("clears credentials when switching GitHub connection type or leaving the platform", async () => {
  const original = state.http.GET.getMockImplementation()!;
  state.http.GET.mockImplementation(async (path: string, options: unknown) =>
    path.endsWith("application-account-provider-types")
      ? response({ items: githubDefinitions })
      : original(path, options),
  );
  setup();
  await userEvent.click(
    screen.getByRole("button", { name: new RegExp("^GitHub") }),
  );
  await userEvent.type(
    await screen.findByLabelText("Personal access token"),
    "fictional-pat",
  );
  await userEvent.click(
    screen.getByRole("radio", { name: /GitHub App · Webhook/ }),
  );
  expect(await screen.findByLabelText("App private key")).toBeTruthy();
  expect(screen.queryByLabelText("Personal access token")).toBeNull();
  await userEvent.type(
    screen.getByLabelText("Webhook secret"),
    "fictional-secret",
  );
  await userEvent.click(
    screen.getByRole("radio", { name: /GitHub account · Polling/ }),
  );
  expect(
    (
      (await screen.findByLabelText(
        "Personal access token",
      )) as HTMLInputElement
    ).value,
  ).toBe("");
  await userEvent.click(
    screen.getByRole("button", { name: "Change platform" }),
  );
  expect(screen.queryByLabelText("Personal access token")).toBeNull();
  expect(
    screen.getByRole("button", { name: new RegExp("^Slack") }),
  ).toBeTruthy();
  expect(state.http.POST).not.toHaveBeenCalled();
});

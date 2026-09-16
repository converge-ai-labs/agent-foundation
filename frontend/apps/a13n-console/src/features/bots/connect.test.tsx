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
  useTranslation: () => ({ t: (key: string) => key }),
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

it("separates account reuse from creation without creating a duplicate", async () => {
  state.http.GET.mockResolvedValue(
    response({
      items: [
        { account: { ...account, provider_config: { team_id: "T_EXISTING" } } },
      ],
      next_cursor: null,
    }),
  );
  setup();
  expect(
    screen
      .getByRole("tab", { name: "Use an existing account" })
      .getAttribute("aria-selected"),
  ).toBe("true");
  expect(
    (await screen.findByRole("link", { name: /Pilot/ })).getAttribute("href"),
  ).toBe("/workspace/test/bots/acct_test");
  expect(screen.getByText("Slack · T_EXISTING")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Slack" })).toBeNull();
  await userEvent.click(
    screen.getByRole("tab", { name: "Create a new account" }),
  );
  expect(await screen.findByRole("button", { name: "Slack" })).toBeTruthy();
  expect(screen.getByRole("button", { name: "Feishu" })).toBeTruthy();
  expect(screen.queryByRole("link", { name: /Pilot/ })).toBeNull();
  await userEvent.keyboard("{ArrowLeft}{Enter}");
  expect(await screen.findByRole("link", { name: /Pilot/ })).toBeTruthy();
  expect(state.http.POST).not.toHaveBeenCalled();
});

it("saves a new pilot with reception off and clears credentials before verification", async () => {
  state.http.POST.mockResolvedValue(response(account));
  setup();
  await userEvent.click(
    screen.getByRole("tab", { name: "Create a new account" }),
  );
  await userEvent.click(await screen.findByRole("button", { name: "Slack" }));
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
  await screen.findByText("Configure HTTP events");
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
        name: "Confirm installation and continue",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(
    await screen.findByText(
      "https://events.example.test/connectivity/v1/accounts/acct_test/events",
    ),
  ).toBeTruthy();
});

it("reconciles a lost save using the same command and rejects changed credentials", async () => {
  state.http.POST.mockRejectedValueOnce(
    new TypeError("Network error"),
  ).mockResolvedValue(response(account));
  setup();
  await userEvent.click(
    screen.getByRole("tab", { name: "Create a new account" }),
  );
  await userEvent.click(await screen.findByRole("button", { name: "Slack" }));
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
  await screen.findByText("Configure HTTP events");
  expect(state.http.POST).toHaveBeenCalledTimes(2);
  expect(state.http.POST.mock.calls[1][1].params.header).toEqual(
    state.http.POST.mock.calls[0][1].params.header,
  );
});

it("resumes a saved account without creating another account or claiming a test passed", async () => {
  setup("/workspace/test/bots/connect?account=acct_test&step=test");
  await screen.findByText("Configure HTTP events");
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
  await screen.findByText("Configure HTTP events");
  expect(
    (
      screen.getByRole("button", {
        name: "Confirm installation and continue",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(screen.queryByText("Choose a pilot conversation")).toBeNull();
});

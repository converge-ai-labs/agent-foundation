import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router";
import { BotGroupDetail } from "./group";

const state = vi.hoisted(() => ({
  manage: true,
  http: { GET: vi.fn(), PUT: vi.fn() },
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: state.http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspace/test",
    can: (action: string) =>
      action === "account_target.manage" ? state.manage : false,
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
  name: "Support bot",
  provider_key: "slack",
  provider_config_version: "slack_http_v1",
  provider_config: { team_id: "T1" },
  version: 3,
  credential_generation: 1,
  status: "active",
  receive_enabled: false,
  default_agent_id: "agt_default",
  provider_policy: { interaction_mode: "discussion", reply_mode: "thread" },
  memory: { provider_id: "mp_test" },
};
const target = {
  id: "tgt_test",
  account_id: account.id,
  target_kind: "conversation",
  external_target_id: "C1",
  version: 2,
  receive_enabled: true,
  provider_policy: null,
  config_override: null,
  input_batching: null,
  agent_id: null,
};
const response = (data: unknown) => ({
  data,
  response: new Response(null, { status: 200 }),
});
function setup(suffix = "") {
  render(
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
      <MemoryRouter
        initialEntries={[
          `/workspace/test/bots/acct_test/channels/tgt_test${suffix}`,
        ]}
      >
        <Routes>
          <Route
            path="/workspace/test/bots/:accountId/channels/:targetId/:groupTab?"
            element={<BotGroupDetail />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.resetAllMocks();
  state.manage = true;
  state.http.GET.mockImplementation(async (path: string) =>
    response(
      path.endsWith("/bot/summary")
        ? {
            account,
            memory_settings: {
              account_id: account.id,
              version: 0,
              memory: null,
            },
          }
        : path.endsWith("/{target_id}")
          ? target
          : { items: [], next_cursor: null },
    ),
  );
  state.http.PUT.mockResolvedValue(response({ ...target, version: 3 }));
});
afterEach(cleanup);

it("keeps bot and external organization context while showing inherited configuration", async () => {
  setup();
  expect(await screen.findByRole("heading", { name: "C1" })).toBeTruthy();
  expect(screen.getByText("Slack · T1")).toBeTruthy();
  expect(
    screen.getByRole("link", { name: "Support bot" }).getAttribute("href"),
  ).toBe("/workspace/test/bots/acct_test");
  expect(screen.getByText("Continue an activated discussion")).toBeTruthy();
  expect(screen.getByText("Thread or topic")).toBeTruthy();
  expect(
    screen.getByText(
      "Bot reception is disabled. Enabling this conversation alone will not receive messages.",
    ),
  ).toBeTruthy();
  expect(
    state.http.GET.mock.calls.every(([path]) => !path.includes("memory")),
  ).toBe(true);
});

it("filters conversations by the exact target and does not mount private memory for a builder", async () => {
  setup("/conversations");
  await waitFor(() =>
    expect(
      state.http.GET.mock.calls.some(
        ([path, args]) =>
          path.endsWith("/bot/threads") &&
          args.params.query.target_id === target.id,
      ),
    ).toBe(true),
  );
  await userEvent.click(screen.getByRole("tab", { name: "Memory" }));
  expect(
    await screen.findByText(
      "Only workspace administrators can view and manage conversation memory.",
    ),
  ).toBeTruthy();
  expect(
    state.http.GET.mock.calls.some(([path]) => path.includes("memory-scopes")),
  ).toBe(false);
});

it("does not offer a target editor without management authority", async () => {
  state.manage = false;
  setup();
  await screen.findByRole("heading", { name: "C1" });
  expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  expect(state.http.PUT).not.toHaveBeenCalled();
});

it("does not read target or memory data when the account belongs to another workspace", async () => {
  state.http.GET.mockResolvedValue(
    response({
      account: { ...account, workspace_id: "ws_other" },
      memory_settings: { account_id: account.id, version: 0, memory: null },
    }),
  );
  setup("/memory");
  expect(await screen.findByText("Bot not found")).toBeTruthy();
  expect(state.http.GET).toHaveBeenCalledTimes(1);
});

it("edits typed policy while retaining version and capability overrides", async () => {
  setup();
  await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
  expect(screen.queryByRole("combobox", { name: "Agent" })).toBeNull();
  expect(screen.queryByLabelText("External target ID")).toBeNull();
  await userEvent.click(
    screen.getByRole("switch", { name: "Inherit bot response policy" }),
  );
  expect(
    screen.getByRole("combobox", { name: "When to respond" }),
  ).toBeTruthy();
  await userEvent.click(
    screen.getByRole("combobox", { name: "When to respond" }),
  );
  await userEvent.click(
    await screen.findByRole("option", { name: "All supported human messages" }),
  );
  await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(state.http.PUT).toHaveBeenCalledTimes(1));
  expect(state.http.PUT.mock.calls[0][1]).toMatchObject({
    params: { path: { account_id: account.id, target_id: target.id } },
    body: {
      target_kind: "conversation",
      external_target_id: "C1",
      expected_version: 2,
      agent_id: null,
      input_batching: null,
      config_override: null,
      provider_policy: { interaction_mode: "chat", reply_mode: "thread" },
    },
  });
});

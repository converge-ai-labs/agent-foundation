import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route } from "react-router";
import type { Schema } from "../../shared/api";
import { ApiError } from "../../service-client";
import { BotOverview } from "./overview";
import { BotDetail } from "./page";

const state = vi.hoisted(() => ({ admin: false, GET: vi.fn(), POST: vi.fn() }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http: state }) }));
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
const account: Schema["Account"] = {
  created_at: "2026-09-16T00:00:00Z",
  updated_at: "2026-09-16T00:00:00Z",
  created_by: { principal_type: "user", principal_id: "usr_test" },
  organization_id: "org_test",
  credential_configured: true,
  provider_config_version: "slack_http_v1",
  provider_config: { team_id: "T1" },
  id: "acct_test",
  name: "Support bot",
  workspace_id: "ws_test",
  version: 3,
  credential_generation: 1,
  provider_key: "slack",
  status: "active",
  receive_enabled: true,
  default_agent_id: "agt_test",
  execution_service_account_id: "svc_test",
  reception_scope: "configured_targets",
  provider_policy: { interaction_mode: "discussion", reply_mode: "thread" },
};
const summary = {
  account,
  setup_condition: "receiving",
  configured_target_count: 2,
  external_organization_id: "T1",
  external_organization_name: "Acme",
  checked_at: "2026-09-16T00:00:00Z",
  test_stage: "accepted",
  test_observed_at: "2026-09-16T00:01:00Z",
} as Schema["BotSummary"];
const test = {
  id: "btest_" + "a".repeat(32),
  account_id: account.id,
  account_version: 3,
  credential_generation: 1,
  target_id: "tgt_test",
  target_version: 1,
  external_target_id: "C1",
  created_at: "2026-09-16T00:00:00Z",
  expires_at: "2026-09-16T00:15:00Z",
  stale: false,
  event_received_at: "2026-09-16T00:01:00Z",
  accepted_at: "2026-09-16T00:02:00Z",
  run_id: "run_test",
  session_id: "sess_test",
  thread_id: "thread_test",
  rejection_code: null,
  reply: {
    status: "outcome_unknown",
    started_at: "2026-09-16T00:03:00Z",
    finished_at: "2026-09-16T00:04:00Z",
  },
};
const response = (value: unknown) => ({
  data: value,
  response: new Response(null, { status: 200 }),
});
function setup(value = summary, detail = false) {
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false, gcTime: 0 } },
        })
      }
    >
      <MemoryRouter initialEntries={["/workspace/test/bots/acct_test"]}>
        {detail ? (
          <Routes>
            <Route
              path="/workspace/test/bots/:accountId"
              element={<BotDetail />}
            />
          </Routes>
        ) : (
          <BotOverview summary={value} />
        )}
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.resetAllMocks();
  state.admin = false;
  state.GET.mockImplementation(async (path: string) =>
    response(
      path.endsWith("/summary")
        ? summary
        : { latest: path.endsWith("/tests/latest") ? test : null },
    ),
  );
});
afterEach(cleanup);

it("shows metadata to viewers without mounting private test requests or management actions", async () => {
  setup();
  await screen.findByText("No check is available for the current credentials.");
  expect(screen.getByText("Test accepted · reply unconfirmed")).toBeTruthy();
  expect(screen.getByText("Continue an activated discussion")).toBeTruthy();
  expect(screen.queryByText("Latest setup test")).toBeNull();
  expect(screen.queryByText("Manage memory")).toBeNull();
  expect(state.GET).toHaveBeenCalledTimes(1);
  expect(state.GET.mock.calls[0][0]).toContain("/checks/latest");
  expect(state.POST).not.toHaveBeenCalled();
});

it("shows administrator test stages and unknown delivery without sending a new test", async () => {
  state.admin = true;
  setup();
  await screen.findByText("Reply outcome unknown");
  expect(screen.getByText("Agent execution accepted")).toBeTruthy();
  expect(
    screen.getByText(
      "The platform may have received the reply. Check the conversation before sending another test.",
    ),
  ).toBeTruthy();
  expect(
    screen.getByRole("link", { name: "Open run" }).getAttribute("href"),
  ).toBe(
    "/workspace/test/sessions/sess_test/threads/thread_test/runs/run_test",
  );
  expect(screen.queryByText("Prepare a test message")).toBeNull();
  await userEvent.click(
    screen.getByRole("button", { name: "Refresh observations" }),
  );
  await waitFor(() =>
    expect(
      state.GET.mock.calls.filter(([path]) => path.endsWith("/tests/latest")),
    ).toHaveLength(2),
  );
  expect(state.POST).not.toHaveBeenCalled();
});

it("provides recovery links for incomplete setup without hiding independent memory defaults", async () => {
  state.admin = true;
  setup({
    ...summary,
    setup_condition: "needs_verification",
    configured_target_count: 0,
    account: {
      ...account,
      receive_enabled: false,
      default_agent_id: null,
      execution_service_account_id: null,
      memory: {
        provider_id: "mem_test",
        use_memory: false,
        save_on_request: true,
        timezone: "UTC",
      },
    },
  });
  await screen.findByRole("link", { name: "Choose an agent" });
  expect(
    screen
      .getByRole("link", { name: "Configure conversations" })
      .getAttribute("href"),
  ).toBe("/workspace/test/bots/acct_test/channels");
  expect(
    screen.getByRole("link", { name: "Resume setup" }).getAttribute("href"),
  ).toBe("/workspace/test/bots/connect?account=acct_test");
  expect(screen.getByText("Use memory during conversations")).toBeTruthy();
  expect(
    screen.getByText("Allow explicit save and forget requests"),
  ).toBeTruthy();
});

it("shows verified organization in the shell and rejects a different Workspace before dependent reads", async () => {
  state.GET.mockResolvedValue(
    response({ ...summary, account: { ...account, workspace_id: "ws_other" } }),
  );
  setup(summary, true);
  await screen.findByText("Bot not found");
  expect(state.GET).toHaveBeenCalledTimes(1);
});

it("uses the current summary identity in the Bot heading", async () => {
  setup(summary, true);
  await screen.findByText("Slack · Acme");
  expect(screen.getByRole("heading", { name: "Support bot" })).toBeTruthy();
});

it("keeps denied detailed history distinct from an absent setup test", async () => {
  state.admin = true;
  state.GET.mockImplementation(async (path: string) => {
    if (path.endsWith("/tests/latest"))
      throw new ApiError(
        403,
        "permission_denied",
        "History access denied.",
        {},
        null,
      );
    return response({ latest: null });
  });
  setup();
  await screen.findByText("History access denied.");
  expect(screen.queryByRole("link", { name: "Open run" })).toBeNull();
  expect(screen.queryByText("No setup test recorded")).toBeNull();
  expect(screen.getByText("Test accepted · reply unconfirmed")).toBeTruthy();
});

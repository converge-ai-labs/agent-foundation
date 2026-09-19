import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Schema } from "../../shared/api";
import { BotChecks } from "./checks";

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
  version: 4,
  credential_generation: 2,
  provider_key: "slack",
} as Schema["Account"];
const result = {
  account_id: account.id,
  credential_generation: 2,
  checked_at: "2026-09-16T00:00:00Z",
  installation: {
    app_id: "A1",
    organization_id: "T1",
    organization_name: "Acme workspace",
    bot_id: "U1",
    bot_name: "Acme helper",
    enabled: true,
  },
  conversation_id: null,
  conversation: null,
  error_code: null,
};
const response = (data: unknown) => ({
  data,
  response: new Response(null, { status: 200 }),
});
function setup() {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={cache}>
      <BotChecks account={account} />
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.resetAllMocks();
  state.admin = true;
  state.http.GET.mockResolvedValue(response({ latest: null }));
});
afterEach(cleanup);

it("requires an explicit check and submits the account version", async () => {
  state.http.POST.mockImplementation(async () => {
    state.http.GET.mockResolvedValue(response({ latest: result }));
    return response(result);
  });
  setup();
  await screen.findByText("No check is available for the current credentials.");
  expect(state.http.POST).not.toHaveBeenCalled();
  await userEvent.click(
    screen.getByRole("button", { name: "Check connection" }),
  );
  expect(await screen.findByText("Acme workspace")).toBeTruthy();
  expect(state.http.POST).toHaveBeenCalledWith(
    "/api/v1/application-accounts/{account_id}/bot/checks",
    expect.objectContaining({
      body: { expected_version: 4, conversation_id: undefined },
    }),
  );
  expect(
    screen.getByText(
      "This result reflects the last check. It does not confirm that messages reach the bot, the agent runs, or replies are delivered.",
    ),
  ).toBeTruthy();
});

it("does not offer provider checks to a read-only viewer", async () => {
  state.admin = false;
  setup();
  await screen.findByText("No check is available for the current credentials.");
  expect(screen.queryByRole("button", { name: "Check connection" })).toBeNull();
  expect(state.http.POST).not.toHaveBeenCalled();
});

it("shows failed verification without claiming an identity", async () => {
  state.http.GET.mockResolvedValue(
    response({
      latest: {
        ...result,
        installation: null,
        error_code: "bot_identity_mismatch",
      },
    }),
  );
  setup();
  expect(
    await screen.findByText(
      "These credentials belong to a different app, bot, or workspace. Check the application account identity.",
    ),
  ).toBeTruthy();
  expect(screen.queryByText("Acme workspace")).toBeNull();
});

it("keeps successful app verification separate from missing group membership", async () => {
  state.http.GET.mockResolvedValue(
    response({
      latest: {
        ...result,
        conversation_id: "C1",
        conversation: {
          id: "C1",
          name: "Engineering",
          audience: "private",
          is_member: false,
          is_active: true,
        },
      },
    }),
  );
  setup();
  expect(await screen.findByText("Not a member")).toBeTruthy();
  expect(screen.getByText("Active")).toBeTruthy();
});

import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BotReplies } from "./replies";

const state = vi.hoisted(() => ({ GET: vi.fn(), readAccess: true }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: { GET: state.GET } }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    can: () => state.readAccess,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
const proof = {
  id: "brr_one",
  run_id: "run_one",
  run_attempt_id: "ratt_one",
  target_id: "tgt_one",
  provider_key: "slack",
  status: "succeeded",
  account_version: 3,
  credential_generation: 2,
  started_at: "2026-09-16T10:00:00Z",
  finished_at: "2026-09-16T10:00:01Z",
  receipt: {
    channel_id: "C1",
    message_ts: "2.0",
    root_thread_ts: "1.0",
    request_id: "reply_one",
  },
  error_code: null,
};
const response = (items: unknown[]) => ({
  data: { items, next_cursor: null },
  response: new Response(null, { status: 200 }),
});
function setup() {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false, gcTime: 0 } },
        })
      }
    >
      <BotReplies accountId="acct_one" runId="run_one" />
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.resetAllMocks();
  state.readAccess = true;
  state.GET.mockResolvedValue(response([proof]));
});
afterEach(cleanup);

it("shows provider confirmation and its exact receipt independently from Run state", async () => {
  setup();
  await screen.findByText("Provider confirmed reply");
  expect(screen.getByText("2.0")).toBeTruthy();
  expect(screen.getByText("reply_one")).toBeTruthy();
  expect(screen.getByText("ratt_one")).toBeTruthy();
  expect(
    screen.getByText(
      "Confirmation means the platform accepted the reply. It does not mean a person read it.",
    ),
  ).toBeTruthy();
  expect(state.GET).toHaveBeenCalledWith(
    expect.stringMatching(/\/bot\/replies$/),
    expect.objectContaining({
      params: {
        path: { account_id: "acct_one" },
        query: { run_id: "run_one", cursor: undefined, limit: 20 },
      },
    }),
  );
});

it("refreshes unknown outcomes through reads without offering a resend", async () => {
  state.GET.mockResolvedValue(
    response([{ ...proof, status: "outcome_unknown", receipt: null }]),
  );
  setup();
  await screen.findByText("Reply outcome unknown");
  expect(screen.queryByText("Provider confirmed reply")).toBeNull();
  expect(
    screen.getByText(
      "The platform may have received this reply. Check the conversation before deciding whether another reply is needed. Refreshing does not resend it.",
    ),
  ).toBeTruthy();
  await userEvent.click(
    screen.getByRole("button", { name: "Refresh observations" }),
  );
  expect(state.GET).toHaveBeenCalledTimes(2);
  expect(screen.queryByRole("button", { name: /resend/i })).toBeNull();
});

it("shows an honest empty observation state", async () => {
  state.GET.mockResolvedValue(response([]));
  setup();
  await screen.findByText("No native reply observed for this run");
  expect(screen.queryByText("Provider confirmed reply")).toBeNull();
});

it("does not fetch private reply metadata without application account read access", () => {
  state.readAccess = false;
  setup();
  expect(
    screen.getByText(
      "Reply observations require application account read access.",
    ),
  ).toBeTruthy();
  expect(state.GET).not.toHaveBeenCalled();
});

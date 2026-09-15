import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import type { Schema } from "../../shared/api";
import { PilotTest } from "./pilot-test";

const state = vi.hoisted(() => ({ http: { GET: vi.fn(), POST: vi.fn() } }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: state.http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspace/test",
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    i18n: { resolvedLanguage: "en" },
    t: (key: string, values?: { marker?: string }) =>
      key.replace("{{marker}}", values?.marker ?? ""),
  }),
}));
const account = {
  id: "acct_test",
  version: 3,
  credential_generation: 1,
  receive_enabled: true,
} as Schema["Account"];
const target = {
  id: "tgt_test",
  version: 1,
  receive_enabled: true,
  target_kind: "conversation",
  external_target_id: "C1",
};
const test = {
  id: "btest_" + "a".repeat(32),
  account_id: account.id,
  account_version: 3,
  credential_generation: 1,
  target_id: target.id,
  target_version: 1,
  external_target_id: "C1",
  stale: false,
  created_at: "2026-09-16T01:00:00Z",
  expires_at: "2099-01-01T00:00:00Z",
  event_received_at: null,
  admission_id: null,
  accepted_at: null,
  run_id: null,
  session_id: null,
  thread_id: null,
  steer_id: null,
  rejection_code: null,
  reply: null,
};
const response = (data: unknown) => ({
  data,
  response: new Response(null, { status: 200 }),
});
function setup(value = account) {
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
      <MemoryRouter>
        <PilotTest account={value} back={() => undefined} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.resetAllMocks();
  state.http.GET.mockImplementation(async (path: string) =>
    response(
      path.endsWith("/targets")
        ? { items: [target], next_cursor: null }
        : { latest: null },
    ),
  );
  state.http.POST.mockResolvedValue(response(test));
});
afterEach(cleanup);

it("does not start or send a test merely by opening the wizard", async () => {
  setup();
  await screen.findByRole("button", { name: "Prepare a test message" });
  expect(state.http.POST).not.toHaveBeenCalled();
  await userEvent.click(
    screen.getByRole("button", { name: "Prepare a test message" }),
  );
  expect(
    await screen.findByText(
      `@bot Please reply with exactly this test marker: ${test.id}`,
    ),
  ).toBeTruthy();
  expect(screen.getByText("Awaiting test message")).toBeTruthy();
  expect(screen.getByText("Awaiting execution acceptance")).toBeTruthy();
  expect(state.http.POST).toHaveBeenCalledTimes(1);
  expect(state.http.POST.mock.calls[0][0]).toMatch(/\/bot\/tests$/);
  await userEvent.click(
    screen.getByRole("button", { name: "Refresh observations" }),
  );
  expect(state.http.POST).toHaveBeenCalledTimes(1);
});

it("replays the same command after a lost acknowledgement", async () => {
  state.http.POST.mockRejectedValueOnce(new Error("Connection lost"));
  setup();
  await userEvent.click(
    await screen.findByRole("button", { name: "Prepare a test message" }),
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "Retry preparing test" }),
  );
  await screen.findByText("Awaiting test message");
  expect(state.http.POST.mock.calls[0][1]).toEqual(
    state.http.POST.mock.calls[1][1],
  );
});

it("resumes a steer test with confirmed reply without claiming Run completion", async () => {
  state.http.GET.mockImplementation(async (path: string) =>
    response(
      path.endsWith("/targets")
        ? { items: [target], next_cursor: null }
        : {
            latest: {
              ...test,
              event_received_at: test.created_at,
              accepted_at: test.created_at,
              run_id: "run_test",
              thread_id: "thr_test",
              session_id: "sess_test",
              steer_id: "steer_test",
              reply: {
                status: "succeeded",
                started_at: test.created_at,
                finished_at: test.created_at,
              },
            },
          },
    ),
  );
  setup();
  expect(await screen.findByText("Provider confirmed test reply")).toBeTruthy();
  expect(
    screen.getByText("Delivered to an existing run as a follow-up message."),
  ).toBeTruthy();
  expect(
    screen.getByRole("link", { name: "Open run" }).getAttribute("href"),
  ).toBe("/workspace/test/sessions/sess_test/threads/thr_test/runs/run_test");
  expect(screen.queryByText("Completed")).toBeNull();
  expect(state.http.POST).not.toHaveBeenCalled();
});

it("separates stale configuration, expired receipt, and unknown reply", async () => {
  state.http.GET.mockImplementation(async (path: string) =>
    response(
      path.endsWith("/targets")
        ? { items: [target], next_cursor: null }
        : {
            latest: {
              ...test,
              stale: true,
              expires_at: "2000-01-01T00:00:00Z",
              reply: {
                status: "outcome_unknown",
                started_at: test.created_at,
                finished_at: null,
              },
            },
          },
    ),
  );
  setup();
  expect(
    await screen.findByText(
      "Configuration changed. These observations belong to the previous configuration; prepare a new test.",
    ),
  ).toBeTruthy();
  expect(screen.getByText("Reply outcome unknown")).toBeTruthy();
  expect(
    screen.getByText(
      "No matching message arrived before the deadline. Prepare a new test message.",
    ),
  ).toBeTruthy();
  expect(
    screen.queryByRole("button", { name: "Copy test message" }),
  ).toBeNull();
  expect(screen.queryByText("Provider confirmed test reply")).toBeNull();
});

it("prevents preparing a test with reception disabled", async () => {
  setup({ ...account, receive_enabled: false });
  await waitFor(() =>
    expect(
      screen
        .getByRole("button", { name: "Prepare a test message" })
        .hasAttribute("disabled"),
    ).toBe(true),
  );
  expect(state.http.POST).not.toHaveBeenCalled();
});

import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import type { ReactNode } from "react";
import { BotConversations } from "./conversations";
import { BotDetail } from "./detail";

const state = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http: state }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspace/test",
    can: () => true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
vi.mock("../application-accounts/form", () => ({
  AccountForm: () => <div>Account settings</div>,
}));
vi.mock("./channels", () => ({
  BotChannels: () => <div>Configured groups</div>,
  useChannelNames: () => (value: string) => value,
}));
vi.mock("../application-accounts/credentials", () => ({
  AccountCredentials: () => null,
}));
vi.mock("./memory", () => ({ BotMemory: () => <div>Scoped memory</div> }));
vi.mock("./checks", () => ({
  BotChecks: () => <div>Installation observations</div>,
  useBotCheck: () => ({
    query: {},
    check: { mutate: () => {}, isPending: false },
    result: undefined,
    running: false,
  }),
  useCheckLabel: () => () => "Check connection",
}));

const item = {
  binding_id: "bind_one",
  session_id: "sess_one",
  thread_id: "thread_one",
  run_id: "run_one",
  agent_id: "agt_one",
  run_status: "succeeded",
  updated_at: "2026-09-16T10:00:00Z",
};
const agent = {
  id: "agt_one",
  key: "support-assistant",
  name: "Support assistant",
};
const response = (data: unknown) => ({
  data,
  response: new Response(null, { status: 200 }),
});
function Location() {
  const location = useLocation();
  return (
    <output aria-label="Current route">
      {location.pathname}
      {location.search}
    </output>
  );
}
function setup(child: ReactNode, path = "/") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        {child}
        <Location />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.resetAllMocks();
  const account = {
    id: "acct_one",
    name: "Helper",
    workspace_id: "ws_test",
    provider_key: "slack",
    status: "active",
    receive_enabled: true,
  };
  state.GET.mockImplementation(async (path: string) =>
    response(
      path.endsWith("/threads")
        ? { items: [item], next_cursor: null }
        : path.endsWith("/summary")
          ? {
              account,
              memory_settings: {
                account_id: account.id,
                version: 0,
                memory: null,
              },
              setup_condition: "receiving",
              configured_target_count: 1,
              test_stage: null,
            }
          : path.endsWith("/agents/{agent}")
            ? agent
            : account,
    ),
  );
});
afterEach(cleanup);

it("links to canonical history and keeps Run status separate from delivery", async () => {
  setup(<BotConversations accountId="acct_one" targetId="tgt_one" />);
  const link = await screen.findByRole("link", { name: /thread_one/ });
  expect(link.getAttribute("href")).toBe(
    "/workspace/test/sessions/sess_one/threads/thread_one/runs/run_one",
  );
  const agentLink = await screen.findByRole("link", {
    name: "Support assistant",
  });
  expect(agentLink.getAttribute("href")).toBe(
    "/workspace/test/agents/support-assistant",
  );
  expect(screen.queryByText("agt_one")).toBeNull();
  expect(screen.getByText("Run status")).toBeTruthy();
  expect(
    screen.getByText(
      "Run status describes agent execution. It does not confirm that a reply was delivered to the platform.",
    ),
  ).toBeTruthy();
  expect(state.GET).toHaveBeenCalledWith(
    expect.stringMatching(/\/bot\/threads$/),
    expect.objectContaining({
      params: {
        path: { account_id: "acct_one" },
        query: { target_id: "tgt_one", cursor: undefined, limit: 20 },
      },
    }),
  );
});

it("uses the server cursor without loading every conversation", async () => {
  state.GET.mockImplementation(async (_path: string, options) =>
    response(
      _path.endsWith("/agents/{agent}")
        ? agent
        : {
            items: [
              {
                ...item,
                thread_id: options.params.query.cursor
                  ? "thread_two"
                  : "thread_one",
              },
            ],
            next_cursor: options.params.query.cursor ? null : "opaque_next",
          },
    ),
  );
  setup(<BotConversations accountId="acct_one" />);
  await screen.findByRole("link", { name: /thread_one/ });
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByRole("link", { name: /thread_two/ });
  expect(
    state.GET.mock.calls.filter(([path]) => path.endsWith("/threads")),
  ).toHaveLength(2);
});

it("does not misreport denied history as an empty conversation list", async () => {
  state.GET.mockResolvedValue({
    error: {
      error: { code: "permission_denied", message: "History access denied." },
    },
    response: new Response(null, { status: 403 }),
  });
  setup(<BotConversations accountId="acct_one" />);
  await screen.findByRole("alert");
  expect(screen.queryByText("No visible bot conversations")).toBeNull();
  expect(screen.queryByRole("link", { name: /thread_one/ })).toBeNull();
});

it("opens and navigates canonical Bot subroutes", async () => {
  setup(
    <Routes>
      <Route
        path="/workspace/test/bots/:accountId/:botTab?"
        element={<BotDetail />}
      />
    </Routes>,
    "/workspace/test/bots/acct_one/conversations",
  );
  await screen.findByRole("link", { name: /thread_one/ });
  expect(
    screen
      .getByRole("tab", { name: "Conversations" })
      .getAttribute("aria-selected"),
  ).toBe("true");
  await userEvent.click(screen.getByRole("tab", { name: /Channels/ }));
  await waitFor(() =>
    expect(screen.getByLabelText("Current route").textContent).toBe(
      "/workspace/test/bots/acct_one/channels",
    ),
  );
  expect(screen.getByText("Configured groups")).toBeTruthy();
  await userEvent.click(screen.getByRole("tab", { name: "Memory" }));
  await waitFor(() =>
    expect(screen.getByLabelText("Current route").textContent).toBe(
      "/workspace/test/bots/acct_one/memory",
    ),
  );
});

it("keeps the selected memory scope in the URL of the memory tab", async () => {
  setup(
    <Routes>
      <Route
        path="/workspace/test/bots/:accountId/:botTab?"
        element={<BotDetail />}
      />
    </Routes>,
    "/workspace/test/bots/acct_one/memory?memory_scope=mscope_one",
  );
  await screen.findByText("Scoped memory");
  expect(screen.getByLabelText("Current route").textContent).toBe(
    "/workspace/test/bots/acct_one/memory?memory_scope=mscope_one",
  );
  expect(state.GET).toHaveBeenCalledTimes(1);
});

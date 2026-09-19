import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { BotsPage } from "./list";

const state = vi.hoisted(() => ({ manage: false, get: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: { GET: state.get } }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspace/test",
    can: () => state.manage,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
const row = {
  account: {
    id: "acct_test",
    name: "Support bot",
    provider_key: "slack",
    status: "active",
    default_agent_id: "agt_test",
    updated_at: "2026-09-16T00:00:00Z",
  },
  external_organization_id: "T1",
  external_organization_name: "Acme",
  setup_condition: "reception_off",
  configured_target_count: 3,
  checked_at: "2026-09-16T00:00:00Z",
  test_stage: "accepted",
  test_observed_at: "2026-09-16T00:01:00Z",
};
const response = (value: unknown) => ({
  data: value,
  response: new Response(null, { status: 200 }),
});
function setup() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false, gcTime: 0 } },
        })
      }
    >
      <MemoryRouter>
        <BotsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
const botCalls = () =>
  state.get.mock.calls.filter(([path]) => path.endsWith("/bots"));
beforeEach(() => {
  vi.resetAllMocks();
  state.manage = false;
  state.get.mockImplementation(async (path: string) =>
    response(
      path.endsWith("/agents/{agent}")
        ? { id: "agt_test", key: "support-agent", name: "Support agent" }
        : { items: [row], next_cursor: null },
    ),
  );
});
afterEach(cleanup);

it("shows permitted metadata with key-based agent links and no administrator actions", async () => {
  setup();
  await screen.findByText("Support bot");
  expect(screen.getByText("Slack · Acme")).toBeTruthy();
  expect(screen.getByText("Message responses off")).toBeTruthy();
  expect(screen.getByRole("link", { name: "3" }).getAttribute("href")).toBe(
    "/workspace/test/bots/acct_test/channels",
  );
  expect(
    (await screen.findByRole("link", { name: "Support agent" })).getAttribute(
      "href",
    ),
  ).toBe("/workspace/test/agents/support-agent");
  expect(screen.queryByText("agt_test")).toBeNull();
  expect(screen.queryByText("Resume setup")).toBeNull();
  expect(screen.queryByText("Connect a bot")).toBeNull();
  expect(botCalls()).toHaveLength(1);
  expect(state.get.mock.calls[0][0]).toBe(
    "/api/v1/workspaces/{workspace}/bots",
  );
});

it.each([
  ["Feishu", "lark"],
  ["GitHub", "github"],
])(
  "queries the server as the search settles and resets pagination for %s",
  async (label, platform) => {
    const user = userEvent.setup();
    state.get.mockImplementation(
      async (
        path: string,
        args: { params: { query: { search?: string; cursor?: string } } },
      ) =>
        response(
          path.endsWith("/agents/{agent}")
            ? { id: "agt_test", key: "support-agent", name: "Support agent" }
            : {
                items: [row],
                next_cursor: args.params.query.cursor ? null : "page-two",
              },
        ),
    );
    setup();
    await screen.findByText("Support bot");
    await user.click(screen.getByRole("button", { name: "Next" }));
    await waitFor(() =>
      expect(botCalls().at(-1)?.[1].params.query.cursor).toBe("page-two"),
    );
    await user.type(
      screen.getByRole("searchbox", { name: "Search bots" }),
      "  Acme  ",
    );
    await waitFor(() =>
      expect(botCalls().at(-1)?.[1].params.query).toMatchObject({
        search: "Acme",
        cursor: undefined,
      }),
    );
    screen.getByRole("combobox", { name: "Platform" }).focus();
    await user.keyboard("{ArrowDown}");
    await user.click(await screen.findByRole("option", { name: label }));
    await waitFor(() =>
      expect(botCalls().at(-1)?.[1].params.query).toMatchObject({
        platform,
        search: "Acme",
        cursor: undefined,
      }),
    );
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() =>
      expect(
        (
          screen.getByRole("searchbox", {
            name: "Search bots",
          }) as HTMLInputElement
        ).value,
      ).toBe(""),
    );
  },
);

it("distinguishes filtered emptiness from initial setup and exposes the resume action only to administrators", async () => {
  state.manage = true;
  const user = userEvent.setup();
  setup();
  await screen.findByText("Support bot");
  await user.click(screen.getByRole("button", { name: "Bot actions" }));
  expect(
    (
      await screen.findByRole("menuitem", { name: "Resume setup" })
    ).getAttribute("href"),
  ).toBe("/workspace/test/bots/connect?account=acct_test");
  await user.keyboard("{Escape}");
  state.get.mockResolvedValue(response({ items: [], next_cursor: null }));
  await user.type(
    screen.getByRole("searchbox", { name: "Search bots" }),
    "Missing",
  );
  await screen.findByText("No matching bots");
  expect(screen.queryByText("No bots connected")).toBeNull();
});

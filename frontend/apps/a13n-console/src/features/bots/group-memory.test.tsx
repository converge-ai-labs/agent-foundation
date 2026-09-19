import type { BotAccount } from "./account";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import type { Schema } from "../../shared/api";
import { BotGroupMemory } from "./memory";

const state = vi.hoisted(() => ({
  admin: true,
  scope: true,
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
  version: 3,
  workspace_id: "ws_test",
  provider_key: "slack",
  memoryVersion: 1,
  memory: { provider_id: "mp_test", timezone: "UTC" },
} as BotAccount;
const target = {
  id: "tgt_test",
  account_id: account.id,
  target_kind: "conversation",
  external_target_id: "C1",
} as Schema["AccountTarget"];
const scope = {
  id: "mscope_correct",
  account_id: account.id,
  provider_id: "mp_test",
  external_conversation_id: "C1",
  name: "Support",
  audience: "private",
  version: 3,
  enabled: false,
  use_memory: true,
  save_on_request: false,
  timezone: "America/New_York",
};
const response = (data: unknown) => ({
  data,
  response: new Response(null, { status: 200 }),
});
function setup() {
  const cache = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter initialEntries={["/?memory_scope=mscope_wrong"]}>
        <BotGroupMemory account={account} target={target} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return cache;
}
beforeEach(() => {
  vi.resetAllMocks();
  state.admin = true;
  state.scope = true;
  state.http.GET.mockImplementation(async (path: string) => {
    if (path.endsWith("/memory-scopes"))
      return response({ items: state.scope ? [scope] : [], next_cursor: null });
    if (path.endsWith("/index"))
      return response({ text: "MEMORY.md", entries: [], next_cursor: null });
    if (path.endsWith("/checks/latest")) return response({ latest: null });
    return response({ items: [], next_cursor: null });
  });
  state.http.POST.mockResolvedValue(response(scope));
});
afterEach(cleanup);

it("uses the exact target lookup and ignores a different scope in the URL", async () => {
  setup();
  await screen.findByText("Support");
  const scopeCalls = state.http.GET.mock.calls.filter(([path]) =>
    path.endsWith("/memory-scopes"),
  );
  expect(scopeCalls).toHaveLength(1);
  expect(scopeCalls[0][1].params.query).toEqual({
    provider_id: "mp_test",
    target_id: "tgt_test",
    limit: 1,
  });
  expect(
    state.http.GET.mock.calls
      .filter(([, args]) => args.params.path.scope_id)
      .every(([, args]) => args.params.path.scope_id === "mscope_correct"),
  ).toBe(true);
  expect(
    state.http.GET.mock.calls.some(([path]) => path.endsWith("/{document_id}")),
  ).toBe(false);
});

it("shows unconfigured group memory without displaying an empty working store", async () => {
  state.scope = false;
  setup();
  expect(
    await screen.findByText("Group memory is not configured"),
  ).toBeTruthy();
  expect(screen.queryByRole("region", { name: "Memory details" })).toBeNull();
  expect(
    state.http.GET.mock.calls.some(([path]) => path.endsWith("/documents")),
  ).toBe(false);
});

it("does not fetch memory or its directory for a viewer", () => {
  state.admin = false;
  setup();
  expect(
    screen.getByText(
      "Only workspace administrators can view and manage conversation memory.",
    ),
  ).toBeTruthy();
  expect(state.http.GET).not.toHaveBeenCalled();
});

it("configures only this target and preserves existing toggles and the draft version", async () => {
  const cache = setup();
  await userEvent.click(
    await screen.findByRole("button", { name: "Group memory settings" }),
  );
  await waitFor(() =>
    expect(
      screen
        .getByRole("button", { name: "Save changes" })
        .hasAttribute("disabled"),
    ).toBe(false),
  );
  expect(
    screen
      .getByRole("switch", { name: "Enable group memory" })
      .getAttribute("aria-checked"),
  ).toBe("false");
  expect(
    screen
      .getByRole("switch", {
        name: "Allow saving or deleting memory through chat",
      })
      .getAttribute("aria-checked"),
  ).toBe("false");
  const readToggle = screen.getByRole("switch", {
    name: "Refer to memory when answering",
  });
  const writeToggle = screen.getByRole("switch", {
    name: "Allow saving or deleting memory through chat",
  });
  expect(screen.queryByRole("textbox", { name: "Time zone" })).toBeNull();
  expect(readToggle.getAttribute("aria-disabled") === "true").toBe(true);
  expect(writeToggle.getAttribute("aria-disabled") === "true").toBe(true);
  await userEvent.click(
    screen.getByRole("switch", { name: "Enable group memory" }),
  );
  expect(readToggle.getAttribute("aria-disabled") === "true").toBe(false);
  expect(writeToggle.getAttribute("aria-disabled") === "true").toBe(false);
  expect(readToggle.getAttribute("aria-checked")).toBe("true");
  expect(writeToggle.getAttribute("aria-checked")).toBe("false");
  await userEvent.click(
    screen.getByRole("switch", { name: "Enable group memory" }),
  );
  cache.setQueryData(
    ["bot-memory-configure-options", account.id, "mp_test", target.id],
    { targets: [target], scopes: [{ ...scope, version: 99 }] },
  );
  await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(state.http.POST).toHaveBeenCalledTimes(1));
  expect(state.http.POST.mock.calls[0][1].body).toEqual({
    external_conversation_id: "C1",
    expected_version: 3,
    auto_organize: false,
    visibility: "group",
    enabled: false,
    use_memory: true,
    save_on_request: false,
    timezone: "America/New_York",
  });
  expect(
    state.http.GET.mock.calls.some(([path]) => path.endsWith("/targets")),
  ).toBe(false);
});

it("saves one-way visibility with the current scope version and preserves the private default", async () => {
  setup();
  await userEvent.click(
    await screen.findByRole("button", { name: "Group memory settings" }),
  );
  const choice = await screen.findByRole("combobox", {
    name: "Who can read this group's memory?",
  });
  expect(choice.textContent).toContain("Only this group");
  expect(
    screen.queryByText(/Limited to groups connected to this bot/),
  ).toBeNull();
  expect(
    state.http.GET.mock.calls.some(([path]) => path.includes("/bot/checks")),
  ).toBe(false);
  await userEvent.click(choice);
  await userEvent.click(
    await screen.findByRole("option", {
      name: "All connected channels in this Slack workspace",
    }),
  );
  expect(
    screen.getByText(/Limited to groups connected to this bot/),
  ).toBeTruthy();
  expect(screen.getByText(/Their private memory stays private/)).toBeTruthy();
  expect(screen.getByText(/This includes historical memory/)).toBeTruthy();
  await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(state.http.POST).toHaveBeenCalledOnce());
  expect(state.http.POST.mock.calls[0][1].body).toMatchObject({
    visibility: "installation",
    expected_version: 3,
    external_conversation_id: "C1",
  });
});

it("canceling visibility changes does not save a grant", async () => {
  setup();
  await userEvent.click(
    await screen.findByRole("button", { name: "Group memory settings" }),
  );
  await userEvent.click(
    await screen.findByRole("combobox", {
      name: "Who can read this group's memory?",
    }),
  );
  await userEvent.click(
    await screen.findByRole("option", {
      name: "All connected channels in this Slack workspace",
    }),
  );
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(state.http.POST).not.toHaveBeenCalled();
});

import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { useState, type ReactNode } from "react";
import type { Schema } from "../../shared/api";
import { ConversationPicker } from "./conversation-picker";
import { BotPilot } from "./pilot";

const state = vi.hoisted(() => ({ http: { GET: vi.fn(), POST: vi.fn() } }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: state.http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspace/test",
    can: () => true,
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
  version: 4,
  credential_generation: 2,
  status: "active",
  receive_enabled: false,
  reception_scope: "configured_targets",
  default_agent_id: "agt_test",
  execution_service_account_id: "sa_test",
} as Schema["Account"];
const target = {
  id: "tgt_test",
  target_kind: "conversation",
  external_target_id: "C1",
  receive_enabled: true,
  version: 7,
} as Schema["AccountTarget"];
const response = (data: unknown) => ({
  data,
  response: new Response(null, { status: 200 }),
});
function setup(child: ReactNode) {
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
      <MemoryRouter>{child}</MemoryRouter>
    </QueryClientProvider>,
  );
}
function Picker() {
  const [value, setValue] = useState("");
  return (
    <ConversationPicker account={account} value={value} onChange={setValue} />
  );
}
beforeEach(() => {
  vi.resetAllMocks();
  state.http.GET.mockImplementation(async (path: string) => {
    if (path.endsWith("/targets"))
      return response({ items: [target], next_cursor: null });
    if (path.endsWith("/agents"))
      return response({
        items: [{ id: "agt_test", name: "Helper" }],
        next_cursor: null,
      });
    if (path.endsWith("/service-accounts"))
      return response({
        items: [{ id: "sa_test", name: "Execution", status: "active" }],
        next_cursor: null,
      });
    if (path.endsWith("/checks/latest")) return response({ latest: null });
    return response({
      items: [{ id: "C1", name: "Engineering" }],
      cursor: "page-2",
    });
  });
});
afterEach(cleanup);

it("discovers on demand, paginates, and preserves a manually entered conversation", async () => {
  setup(<Picker />);
  await userEvent.type(
    screen.getByRole("textbox", { name: "Pilot conversation ID" }),
    "C-private",
  );
  expect(state.http.GET).not.toHaveBeenCalled();
  await userEvent.click(
    screen.getByRole("button", { name: "Find conversations" }),
  );
  await screen.findByRole("button", { name: /Engineering/ });
  expect(
    (
      screen.getByRole("textbox", {
        name: "Pilot conversation ID",
      }) as HTMLInputElement
    ).value,
  ).toBe("C-private");
  await userEvent.click(screen.getByRole("button", { name: /Engineering/ }));
  expect(
    (
      screen.getByRole("textbox", {
        name: "Pilot conversation ID",
      }) as HTMLInputElement
    ).value,
  ).toBe("C1");
  await userEvent.click(screen.getByRole("button", { name: "Next" }));
  await waitFor(() =>
    expect(state.http.GET).toHaveBeenLastCalledWith(
      "/api/v1/application-accounts/{account_id}/bot/conversations",
      expect.objectContaining({
        params: {
          path: { account_id: "acct_test" },
          query: { limit: 100, cursor: "page-2" },
        },
      }),
    ),
  );
});

it("allows manual ID entry when discovery is denied", async () => {
  state.http.GET.mockRejectedValue(
    new Error("Provider permission is missing."),
  );
  setup(<Picker />);
  await userEvent.click(
    screen.getByRole("button", { name: "Find conversations" }),
  );
  await screen.findByText("Provider permission is missing.");
  await userEvent.type(
    screen.getByRole("textbox", { name: "Pilot conversation ID" }),
    "C-private",
  );
  expect(
    (
      screen.getByRole("textbox", {
        name: "Pilot conversation ID",
      }) as HTMLInputElement
    ).value,
  ).toBe("C-private");
  expect(
    screen.queryByText(
      "No matching conversations on this page. You can still enter an ID above.",
    ),
  ).toBeNull();
});

it("requires an explicit activation and sends the reviewed target and account versions", async () => {
  const success = vi.fn();
  state.http.POST.mockResolvedValue(
    response({ ...account, version: 5, receive_enabled: true }),
  );
  setup(
    <BotPilot
      account={account}
      onSuccess={success}
      onBack={vi.fn()}
      reload={vi.fn()}
    />,
  );
  const button = await screen.findByRole("button", {
    name: "Verify and enable reception",
  });
  expect(state.http.POST).not.toHaveBeenCalled();
  await userEvent.click(button);
  await waitFor(() => expect(success).toHaveBeenCalled());
  expect(state.http.POST).toHaveBeenCalledWith(
    "/api/v1/application-accounts/{account_id}/bot/activate",
    expect.objectContaining({
      body: {
        expected_version: 4,
        target_id: "tgt_test",
        target_version: 7,
        conversation_id: "C1",
        agent_id: "agt_test",
        execution_service_account_id: "sa_test",
        policy: { interaction_mode: "mention", reply_mode: "auto" },
      },
    }),
  );
});

it("keeps the form after a failed live check and does not claim activation", async () => {
  const success = vi.fn();
  state.http.POST.mockRejectedValue(
    new Error("The bot is not a member of this conversation."),
  );
  setup(
    <BotPilot
      account={account}
      onSuccess={success}
      onBack={vi.fn()}
      reload={vi.fn()}
    />,
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "Verify and enable reception" }),
  );
  await screen.findByText("The bot is not a member of this conversation.");
  expect(success).not.toHaveBeenCalled();
  expect(
    screen.getByRole("button", { name: "Verify and enable reception" }),
  ).toBeTruthy();
});

it("does not enable multiple configured conversations as a single pilot", async () => {
  const original = state.http.GET.getMockImplementation()!;
  state.http.GET.mockImplementation(async (path: string, options: unknown) =>
    path.endsWith("/targets")
      ? response({
          items: [
            target,
            { ...target, id: "tgt_second", external_target_id: "C2" },
          ],
          next_cursor: null,
        })
      : original(path, options),
  );
  setup(
    <BotPilot
      account={account}
      onSuccess={vi.fn()}
      onBack={vi.fn()}
      reload={vi.fn()}
    />,
  );
  await screen.findByText(
    "Configure exactly one pilot conversation that inherits its account settings.",
  );
  expect(
    screen.queryByRole("button", { name: "Verify and enable reception" }),
  ).toBeNull();
  expect(state.http.POST).not.toHaveBeenCalled();
});

it("does not silently adopt a refreshed account version while the user edits", async () => {
  function Parent() {
    const [current, setCurrent] = useState(account);
    return (
      <>
        <button onClick={() => setCurrent({ ...account, version: 9 })}>
          Refresh account
        </button>
        <BotPilot
          account={current}
          onSuccess={vi.fn()}
          onBack={vi.fn()}
          reload={vi.fn()}
        />
      </>
    );
  }
  state.http.POST.mockRejectedValue(new Error("The account version changed."));
  setup(<Parent />);
  await screen.findByRole("button", { name: "Verify and enable reception" });
  await userEvent.click(
    screen.getByRole("button", { name: "Refresh account" }),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Verify and enable reception" }),
  );
  await screen.findByText("The account version changed.");
  expect(state.http.POST.mock.calls[0][1].body.expected_version).toBe(4);
});

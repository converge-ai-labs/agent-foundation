import type { BotAccount } from "./account";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Schema } from "../../shared/api";
import { MemorySettings } from "./memory-settings";

const state = vi.hoisted(() => ({
  typesFailed: false,
  empty: false,
  created: false,
  manage: true,
  http: { GET: vi.fn(), PUT: vi.fn(), POST: vi.fn() },
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: state.http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    can: () => state.manage,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
const account = {
  id: "acct_test",
  version: 3,
  workspace_id: "ws_test",
  memoryVersion: 1,
  memory: { provider_id: "mp_legacy", timezone: "UTC" },
} as BotAccount;
const response = (data: unknown) => ({
  data,
  response: new Response(null, { status: 200 }),
});
beforeEach(() => {
  vi.resetAllMocks();
  state.typesFailed = false;
  state.empty = false;
  state.created = false;
  state.manage = true;
  state.http.GET.mockImplementation(async (path: string) => {
    if (path.endsWith("memory-provider-types")) {
      if (state.typesFailed) throw new Error("Catalog unavailable");
      return response({
        items: [
          { type: "legacy", supports_documents: false },
          {
            type: "documents",
            display_name: "Documents",
            supports_documents: true,
            configuration_schema: { type: "object" },
            credential_schema: { type: "object" },
          },
        ].filter((item) => !state.empty || item.type === "documents"),
      });
    }
    return response({
      items:
        state.empty && !state.created
          ? []
          : [
              {
                id: "mp_legacy",
                name: "Legacy",
                type: "legacy",
                enabled: true,
              },
              {
                id: "mp_documents",
                name: "Documents",
                type: "documents",
                enabled: true,
              },
            ],
      next_cursor: null,
    });
  });
  state.http.PUT.mockResolvedValue(response(account));
  state.http.POST.mockImplementation(async () => {
    state.created = true;
    return response({
      id: "mp_documents",
      name: "Documents",
      type: "documents",
      enabled: true,
    });
  });
});
afterEach(cleanup);
async function setup(selected = account) {
  const cache = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  const reload = vi.fn(async () => {});
  render(
    <QueryClientProvider client={cache}>
      <MemorySettings account={selected} reload={reload} />
    </QueryClientProvider>,
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Memory settings" }),
  );
  if (selected.memory)
    await screen.findByRole("combobox", { name: "Memory storage" });
  return reload;
}
it("blocks unsupported bindings and saves a declared document provider", async () => {
  const reload = await setup();
  expect(
    screen.getByText("Choose a Provider that supports document memory."),
  ).toBeTruthy();
  expect(
    screen
      .getByRole("button", { name: "Save changes" })
      .hasAttribute("disabled"),
  ).toBe(true);
  await userEvent.click(
    screen.getByRole("combobox", { name: "Memory storage" }),
  );
  expect(
    screen
      .getByRole("option", { name: "Legacy · Document memory unsupported" })
      .getAttribute("aria-disabled"),
  ).toBe("true");
  await userEvent.click(screen.getByRole("option", { name: "Documents" }));
  await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(reload).toHaveBeenCalledOnce());
  expect(state.http.PUT).toHaveBeenCalledOnce();
  expect(state.http.PUT.mock.calls[0][1].body.memory.provider_id).toBe(
    "mp_documents",
  );
});
it("lets an administrator disable memory when capability discovery fails", async () => {
  state.typesFailed = true;
  await setup();
  expect(
    screen
      .getByRole("button", { name: "Save changes" })
      .hasAttribute("disabled"),
  ).toBe(true);
  await userEvent.click(
    screen.getByRole("combobox", { name: "Memory storage" }),
  );
  await userEvent.keyboard("{ArrowDown}");
  expect(
    await screen.findByRole("option", { name: "Legacy · Unavailable" }),
  ).toBeTruthy();
  expect(
    screen.queryByRole("option", { name: /Document memory unsupported/ }),
  ).toBeNull();
  await userEvent.keyboard("{Escape}");
  await userEvent.click(screen.getByRole("switch", { name: "Enable memory" }));
  await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(state.http.PUT).toHaveBeenCalledOnce());
  expect(state.http.PUT.mock.calls[0][1].body.memory).toBeNull();
});

it("requires storage before enabling and returns from provider creation without saving the bot", async () => {
  state.empty = true;
  const reload = await setup({ ...account, memory: null });
  await userEvent.click(screen.getByRole("switch", { name: "Enable memory" }));
  await screen.findByText(
    "No compatible memory storage is available. Add storage to continue.",
  );
  expect(
    screen
      .getByRole("button", { name: "Save changes" })
      .hasAttribute("disabled"),
  ).toBe(true);
  await userEvent.click(
    screen.getByRole("button", { name: "Add memory storage" }),
  );
  const editor = await screen.findByRole("dialog", { name: "Add provider" });
  await within(editor).findByRole("textbox", { name: "Name" });
  await userEvent.click(
    within(editor).getByRole("button", { name: "Add provider" }),
  );
  await waitFor(() => expect(state.http.POST).toHaveBeenCalledOnce());
  await waitFor(() =>
    expect(
      screen.getByRole("combobox", { name: "Memory storage" }).textContent,
    ).toContain("Documents"),
  );
  expect(state.http.PUT).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(reload).toHaveBeenCalledOnce());
  expect(state.http.PUT.mock.calls[0][1].body.memory.provider_id).toBe(
    "mp_documents",
  );
});
it("canceling configuration never enables memory", async () => {
  await setup({ ...account, memory: null });
  await userEvent.click(screen.getByRole("switch", { name: "Enable memory" }));
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(state.http.PUT).not.toHaveBeenCalled();
});
it("does not offer provider creation without provider management permission", async () => {
  state.manage = false;
  await setup();
  expect(
    screen.queryByRole("button", { name: "Add memory storage" }),
  ).toBeNull();
});

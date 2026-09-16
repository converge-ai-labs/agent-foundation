import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { Schema } from "../../shared/api";
import { MemorySettings } from "./memory-settings";

const state = vi.hoisted(() => ({
  typesFailed: false,
  http: { GET: vi.fn(), PATCH: vi.fn() },
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: state.http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test" } }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
const account = {
  id: "acct_test",
  version: 3,
  workspace_id: "ws_test",
  memory: { provider_id: "mp_legacy", timezone: "UTC" },
} as Schema["Account"];
const response = (data: unknown) => ({
  data,
  response: new Response(null, { status: 200 }),
});
beforeEach(() => {
  vi.resetAllMocks();
  state.typesFailed = false;
  state.http.GET.mockImplementation(async (path: string) => {
    if (path.endsWith("memory-provider-types")) {
      if (state.typesFailed) throw new Error("Catalog unavailable");
      return response({
        items: [
          { type: "legacy", supports_documents: false },
          { type: "documents", supports_documents: true },
        ],
      });
    }
    return response({
      items: [
        { id: "mp_legacy", name: "Legacy", type: "legacy", enabled: true },
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
  state.http.PATCH.mockResolvedValue(response(account));
});
afterEach(cleanup);
async function setup() {
  const cache = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  const reload = vi.fn(async () => {});
  render(
    <QueryClientProvider client={cache}>
      <MemorySettings account={account} reload={reload} />
    </QueryClientProvider>,
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Memory settings" }),
  );
  await screen.findByRole("combobox", { name: "Memory Provider" });
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
    screen.getByRole("combobox", { name: "Memory Provider" }),
  );
  expect(
    (
      await screen.findByRole("option", {
        name: "Legacy · Document memory unsupported",
      })
    ).getAttribute("aria-disabled"),
  ).toBe("true");
  await userEvent.click(screen.getByRole("option", { name: "Documents" }));
  await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(reload).toHaveBeenCalledOnce());
  expect(state.http.PATCH).toHaveBeenCalledOnce();
  expect(state.http.PATCH.mock.calls[0][1].body.memory.provider_id).toBe(
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
    screen.getByRole("combobox", { name: "Memory Provider" }),
  );
  expect(
    await screen.findByRole("option", { name: "Legacy · Unavailable" }),
  ).toBeTruthy();
  expect(
    screen.queryByRole("option", { name: /Document memory unsupported/ }),
  ).toBeNull();
  await userEvent.click(screen.getByRole("option", { name: "Disabled" }));
  await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(state.http.PATCH).toHaveBeenCalledOnce());
  expect(state.http.PATCH.mock.calls[0][1].body.memory).toBeNull();
});

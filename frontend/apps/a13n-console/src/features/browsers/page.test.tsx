// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import "../../i18n";
import { CreateBrowser } from "./page";

const api = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: api, workspace: () => api }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test" } }),
}));

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

async function open() {
  const user = userEvent.setup();
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter>
        <CreateBrowser onCreated={vi.fn()} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await user.click(screen.getByRole("button", { name: "Create browser" }));
  return within(await screen.findByRole("dialog", { name: "Create browser" }));
}

it.each([
  { providers: [] },
  { providers: [{ id: "bp_off", name: "Disabled", enabled: false }] },
])(
  "offers provider management instead of a create form when none are enabled ($providers)",
  async ({ providers }) => {
    api.GET.mockResolvedValue({
      data: { items: providers, next_cursor: null },
    });
    const dialog = await open();
    expect(
      (
        await dialog.findByRole("link", { name: "Manage providers" })
      ).getAttribute("href"),
    ).toContain("category=browsers");
    expect(dialog.queryByRole("combobox")).toBeNull();
    expect(dialog.queryByRole("textbox", { name: "Name" })).toBeNull();
    expect(dialog.queryByRole("button", { name: "Create browser" })).toBeNull();
  },
);

it("distinguishes a failed provider lookup from an empty catalog", async () => {
  api.GET.mockRejectedValueOnce(new Error("Request failed"));
  api.GET.mockResolvedValueOnce({ data: { items: [], next_cursor: null } });
  const dialog = await open();
  const reload = await dialog.findByRole("button", { name: "Reload" });
  expect(dialog.queryByRole("link", { name: "Manage providers" })).toBeNull();
  await userEvent.click(reload);
  await dialog.findByRole("link", { name: "Manage providers" });
});

it("shows a provider placeholder and prevents submission before selection", async () => {
  api.GET.mockImplementation(async (path: string) => ({
    data: {
      items:
        path === "/api/v1/browser-providers"
          ? [{ id: "bp_on", name: "Example", type: "docker", enabled: true }]
          : [
              {
                type: "docker",
                display_name: "Docker browser",
                supports_stop: true,
              },
            ],
      next_cursor: null,
    },
  }));
  const dialog = await open();
  const provider = await dialog.findByRole("combobox", { name: "Provider" });
  expect(provider.textContent).toContain("Select a provider");
  expect(
    dialog.queryByRole("spinbutton", { name: "Delete after idle (seconds)" }),
  ).toBeNull();
  const name = dialog.getByRole("textbox", { name: "Name" });
  await userEvent.type(name, "Example");
  fireEvent.submit(name.closest("form")!);
  expect(api.POST).not.toHaveBeenCalled();
});

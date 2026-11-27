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
import { BrowsersPage, CreateBrowser } from "./page";

const api = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: api, workspace: () => api }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test" }, can: () => false }),
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

it("offers provider management instead of a create form when the provider is disabled", async () => {
  api.GET.mockResolvedValue({
    data: {
      items: [{ id: "bp_off", name: "Disabled", enabled: false }],
      next_cursor: null,
    },
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
});

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

it("loads a Browser App selection from the URL and keeps attribution in its details", async () => {
  const browser = {
    id: "browser_test",
    name: "Research profile",
    source: "managed",
    app_id: "research.app",
    status: "reserved",
    control: "automation",
    created_at: "2026-11-25T12:00:00Z",
  };
  api.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/{browser_id}")
      ? browser
      : { items: [browser], next_cursor: null },
    response: new Response(null, { headers: { ETag: '"browser_test:1"' } }),
  }));
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter initialEntries={["/browsers?app_id=research.app"]}>
        <BrowsersPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByText(browser.name);
  expect(api.GET).toHaveBeenCalledWith(
    "/api/v1/browsers",
    expect.objectContaining({
      params: { query: expect.objectContaining({ app_id: browser.app_id }) },
    }),
  );
  expect(
    screen.getByRole("row", { name: /Research profile/ }).textContent,
  ).toContain(browser.app_id);
  await userEvent.click(screen.getByText(browser.name));
  const details = within(await screen.findByRole("dialog"));
  expect(
    (await details.findByText(browser.app_id))
      .closest("[title]")
      ?.getAttribute("title"),
  ).toBe(browser.app_id);
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";
import { ModelsPage } from "./list";

const state = vi.hoisted(() => ({ GET: vi.fn(), write: true }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: state, workspace: () => state }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    can: () => state.write,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));

beforeEach(() => {
  state.write = true;
  state.GET.mockImplementation(async (path: string) => ({
    data:
      path === "/api/v1/media-understanding-defaults"
        ? {}
        : { items: [], next_cursor: null },
    response: new Response(),
  }));
});

function setup(search = "") {
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false } },
        })
      }
    >
      <MemoryRouter initialEntries={[`/workspace/ws_test/models${search}`]}>
        <ModelsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return userEvent.setup();
}

it("offers model setup in the empty state while keeping provider navigation", async () => {
  const user = setup();
  await screen.findByText("No models yet");
  const create = screen.getByRole("button", { name: "Add model" });
  expect(create.closest("header")).toBeNull();
  const manage = screen.getByRole("link", { name: "Manage providers" });
  expect(manage.closest("header")).not.toBeNull();
  expect(manage.getAttribute("href")).toBe(
    "/workspace/ws_test/settings/providers?category=models",
  );
  await user.click(create);
  expect(
    await screen.findByRole("dialog", { name: "Connect a new provider" }),
  ).toBeTruthy();
});

it("keeps creation in the header when filters have no matches", async () => {
  setup("?q=missing");
  await screen.findByText("No matching models");
  expect(
    screen.getByRole("button", { name: "Add model" }).closest("header"),
  ).not.toBeNull();
});

it("does not offer creation to a reader", async () => {
  state.write = false;
  setup();
  await screen.findByText("No models yet");
  expect(screen.queryByRole("button", { name: "Add model" })).toBeNull();
});

it("marks declared inputs and names a model its provider can't serve", async () => {
  state.GET.mockImplementation(async (path: string) => ({
    data:
      path === "/api/v1/media-understanding-defaults"
        ? { image: "team-vision" }
        : path === "/api/v1/model-providers"
          ? {
              items: [
                {
                  id: "prov_off",
                  name: "Retired",
                  type: "ollama",
                  config: {},
                  enabled: false,
                },
              ],
              next_cursor: null,
            }
          : {
              items: [
                {
                  key: "team-vision",
                  name: "Team vision",
                  enabled: true,
                  provider_id: "prov_off",
                  catalog_ref: null,
                  updated_at: "2026-10-01T00:00:00Z",
                  config: {
                    model_name: "vision-upstream",
                    characteristics: {
                      capabilities: [
                        "image_understanding",
                        "document_understanding",
                      ],
                    },
                  },
                },
              ],
              next_cursor: null,
            },
    response: new Response(),
  }));
  setup();
  expect(
    await screen.findByRole("img", { name: "Images · Image default" }),
  ).toBeTruthy();
  expect(screen.getByRole("img", { name: "PDF documents" })).toBeTruthy();
  // One state on one line; the reason is on hover, not a second line.
  const status = screen
    .getByText("Provider disabled")
    .closest('[data-slot="status-pill"]');
  expect(status?.getAttribute("title")).toBe(
    "Agents can't use this model until {{provider}} is enabled.",
  );
  expect(screen.queryByText("state.enabled")).toBeNull();
});

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { MediaUnderstandingDefaults } from "./media-understanding";

const state = vi.hoisted(() => ({
  manage: true,
  http: { GET: vi.fn(), PUT: vi.fn() },
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
const initial = {
  workspace_id: "ws_test",
  version: 0,
  image: null,
  video: null,
  audio: null,
};
const response = (data: unknown, etag = '"v0"') => ({
  data,
  response: new Response(null, { status: 200, headers: { ETag: etag } }),
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
      <MediaUnderstandingDefaults />
    </QueryClientProvider>,
  );
  return cache;
}
beforeEach(() => {
  vi.resetAllMocks();
  state.manage = true;
  HTMLElement.prototype.scrollIntoView = () => {};
  state.http.GET.mockImplementation(async (path: string) => {
    if (path.endsWith("/media-understanding-defaults"))
      return response(initial);
    if (path.endsWith("/model-providers"))
      return response({
        items: [
          { id: "provider", enabled: true },
          { id: "disabled", enabled: false },
        ],
        next_cursor: null,
      });
    return response({
      items: [
        {
          key: "vision",
          name: "Vision",
          enabled: true,
          provider_id: "provider",
          declarations: { capabilities: ["image_understanding"] },
        },
        {
          key: "text",
          name: "Text",
          enabled: true,
          provider_id: "provider",
          declarations: {},
        },
        {
          key: "disabled",
          name: "Disabled",
          enabled: false,
          provider_id: "provider",
          declarations: { capabilities: ["image_understanding"] },
        },
        {
          key: "offline",
          name: "Offline",
          enabled: true,
          provider_id: "disabled",
          declarations: { capabilities: ["image_understanding"] },
        },
      ],
      next_cursor: null,
    });
  });
  state.http.PUT.mockResolvedValue(
    response({ ...initial, version: 1, image: "vision" }, '"v1"'),
  );
});
afterEach(cleanup);

it("offers only compatible enabled models and saves the full selection with its ETag", async () => {
  setup();
  const user = userEvent.setup();
  const image = await screen.findByRole("combobox", {
    name: "Image understanding",
  });
  await waitFor(() => expect(image.hasAttribute("disabled")).toBe(false));
  await user.click(image);
  expect(screen.queryByRole("option", { name: "Text" })).toBeNull();
  expect(screen.queryByRole("option", { name: "Disabled" })).toBeNull();
  expect(screen.queryByRole("option", { name: "Offline" })).toBeNull();
  await user.click(screen.getByRole("option", { name: "Vision" }));
  await user.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(state.http.PUT).toHaveBeenCalledWith(
      "/api/v1/workspaces/{workspace}/media-understanding-defaults",
      {
        params: {
          path: { workspace: "ws_test" },
          header: { "If-Match": '"v0"' },
        },
        body: { image: "vision", video: null, audio: null },
      },
    ),
  );
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull(),
  );
});

it("preserves a conflicting draft until discard reloads the saved selection", async () => {
  state.http.PUT.mockRejectedValue(new Error("The defaults changed"));
  const cache = setup();
  const user = userEvent.setup();
  const image = await screen.findByRole("combobox", {
    name: "Image understanding",
  });
  await waitFor(() => expect(image.hasAttribute("disabled")).toBe(false));
  await user.click(image);
  await user.click(screen.getByRole("option", { name: "Vision" }));
  cache.setQueryData(["media-understanding-defaults", "ws_test"], {
    value: { ...initial, version: 2 },
    etag: '"v2"',
  });
  await user.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("The defaults changed");
  expect(state.http.PUT.mock.calls[0][1].params.header["If-Match"]).toBe(
    '"v0"',
  );
  expect(image.textContent).toContain("Vision");
  await user.click(screen.getByRole("button", { name: "Discard" }));
  await waitFor(() => expect(image.textContent).toContain("Not configured"));
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
});

it("shows defaults without edit controls to readers", async () => {
  state.manage = false;
  setup();
  const image = await screen.findByRole("combobox", {
    name: "Image understanding",
  });
  expect(image.hasAttribute("disabled")).toBe(true);
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
});

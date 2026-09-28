import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiError } from "../../service-client";
import type { Schema } from "../../shared/api";
import { MediaUnderstandingDefaults } from "./media-understanding";
import { MediaUnderstandingFields } from "./media-understanding-fields";

const state = vi.hoisted(() => ({
  admin: true,
  http: { GET: vi.fn(), PUT: vi.fn() },
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: state.http, workspace: () => state.http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    organization: { id: "org_test" },
    workspace: { id: "ws_test" },
    basePath: "/workspace/ws_test",
    can: (verb: string) => verb !== "admin" || state.admin,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, string>) =>
      Object.entries(values ?? {}).reduce(
        (text, [name, value]) => text.replaceAll(`{{${name}}}`, value),
        key,
      ),
  }),
}));
const initial: Schema["MediaDefaults"] = {
  id: "ws_test",
  version: 0,
  image: null,
  video: null,
  audio: null,
};
const response = (data: unknown, etag = '"ws_test:0"') => ({
  data,
  response: new Response(null, { status: 200, headers: { ETag: etag } }),
});
const model = (
  key: string,
  name: string,
  capabilities: string[],
  overrides: Record<string, unknown> = {},
) => ({
  key,
  name,
  enabled: true,
  provider_id: "provider",
  config: { model_name: `${key}-upstream`, characteristics: { capabilities } },
  ...overrides,
});
const models = [
  model("model-vision", "Vision", ["image_understanding"]),
  model("model-text", "Text", []),
  model("model-disabled", "Disabled", ["image_understanding"], {
    enabled: false,
  }),
  model("model-offline", "Offline", ["image_understanding"], {
    provider_id: "disabled",
  }),
];
const defaultsPath = "/api/v1/media-understanding-defaults";
function setup(content = <MediaUnderstandingDefaults />) {
  const cache = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>{content}</MemoryRouter>
    </QueryClientProvider>,
  );
}
async function imageSelect() {
  const image = await screen.findByRole("combobox", {
    name: "Image understanding",
  });
  await waitFor(() => expect(image.hasAttribute("disabled")).toBe(false));
  return image;
}
/** The popup mounts after its own effects, so every option read waits for it. */
async function chooseImage(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await imageSelect());
  return screen.findByRole("option", { name: /^Vision/ });
}
let saved = initial;
beforeEach(() => {
  vi.resetAllMocks();
  state.admin = true;
  saved = initial;
  HTMLElement.prototype.scrollIntoView = () => {};
  state.http.GET.mockImplementation(async (path: string) => {
    if (path === defaultsPath)
      return response(saved, `"ws_test:${saved.version}"`);
    if (path.endsWith("/model-providers"))
      return response({
        items: [
          { id: "provider", name: "Local", enabled: true },
          { id: "disabled", name: "Retired", enabled: false },
        ],
        next_cursor: null,
      });
    return response({ items: models, next_cursor: null });
  });
  state.http.PUT.mockResolvedValue(
    response({ ...initial, version: 1, image: "model-vision" }, '"ws_test:1"'),
  );
});
afterEach(cleanup);

it("offers only compatible enabled models and saves the whole selection on change", async () => {
  setup();
  const user = userEvent.setup();
  const image = await imageSelect();
  const vision = await chooseImage(user);
  expect(screen.queryByRole("option", { name: /Text/ })).toBeNull();
  expect(screen.queryByRole("option", { name: /Disabled/ })).toBeNull();
  expect(screen.queryByRole("option", { name: /Offline/ })).toBeNull();
  await user.click(vision);
  await waitFor(() =>
    expect(state.http.PUT).toHaveBeenCalledWith(defaultsPath, {
      headers: { "If-Match": '"ws_test:0"' },
      body: { image: "model-vision", video: null, audio: null },
    }),
  );
  await waitFor(() => expect(image.textContent).toContain("Vision"));
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
});

it("shows the row saving and keeps the other rows out of reach", async () => {
  let settle = () => {};
  state.http.PUT.mockReturnValue(
    new Promise((resolve) => {
      settle = () =>
        resolve(
          response(
            { ...initial, version: 1, image: "model-vision" },
            '"ws_test:1"',
          ),
        );
    }),
  );
  setup();
  const user = userEvent.setup();
  await user.click(await chooseImage(user));
  await screen.findByText("Saving…");
  expect(
    screen
      .getByRole("combobox", { name: "Audio understanding" })
      .hasAttribute("disabled"),
  ).toBe(true);
  settle();
  await waitFor(() => expect(screen.queryByText("Saving…")).toBeNull());
});

it("reloads the saved selection when the defaults changed elsewhere", async () => {
  state.http.PUT.mockRejectedValue(
    new ApiError(412, "precondition_failed", "Conflict", {}, null),
  );
  setup();
  const user = userEvent.setup();
  const image = await imageSelect();
  await user.click(await chooseImage(user));
  await screen.findByText(/These defaults changed elsewhere/);
  await waitFor(() => expect(image.textContent).toContain("Not configured"));
  expect(
    state.http.GET.mock.calls.filter((call) => call[0] === defaultsPath),
  ).toHaveLength(2);
});

it("warns on the row whose saved model is no longer eligible", async () => {
  saved = { ...initial, image: "model-disabled" };
  setup();
  const image = await imageSelect();
  expect(image.textContent).toContain("Disabled");
  await screen.findByText(
    /Saved model model-disabled is disabled or no longer declares image understanding\./,
  );
});

it("explains a kind that no enabled model declares", async () => {
  setup();
  await imageSelect();
  expect(
    screen.getByText(/No enabled model declares video understanding\./),
  ).toBeTruthy();
  expect(
    screen
      .getAllByRole("link", { name: "Manage models" })[0]
      .getAttribute("href"),
  ).toBe("/workspace/ws_test/models");
  expect(
    screen.queryByText(/No enabled model declares image understanding\./),
  ).toBeNull();
});

it("shows defaults without edit controls to non-admins", async () => {
  state.admin = false;
  setup();
  const image = await screen.findByRole("combobox", {
    name: "Image understanding",
  });
  expect(image.hasAttribute("disabled")).toBe(true);
  expect(screen.queryByText("Saving…")).toBeNull();
});

it("names what an inherited kind falls back to and keeps a retired selection unselectable", async () => {
  setup(
    <MediaUnderstandingFields
      value={{ image: "retired" }}
      onChange={() => {}}
      inherit={{ label: "Workspace default", describe: () => "Vision" }}
    />,
  );
  const user = userEvent.setup();
  const image = await imageSelect();
  expect(image.textContent).toContain("retired");
  await user.click(image);
  const inherited = await screen.findByRole("option", {
    name: /Workspace default/,
  });
  expect(inherited.textContent).toContain("Vision");
  expect(
    screen
      .getByRole("option", { name: /retired/ })
      .getAttribute("aria-disabled"),
  ).toBe("true");
});

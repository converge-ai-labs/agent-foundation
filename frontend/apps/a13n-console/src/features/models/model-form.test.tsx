import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ModelEditor } from "./model-editor";

const state = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  PATCH: vi.fn(),
  close: vi.fn(),
  catalog: {} as Record<string, unknown>,
  types: [] as unknown[],
  providers: [] as unknown[],
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({
    http: { GET: state.GET, POST: state.POST, PATCH: state.PATCH },
    workspace: () => ({ GET: state.GET, POST: state.POST, PATCH: state.PATCH }),
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    organization: { id: "org_test" },
    workspace: { id: "ws_test" },
  }),
}));
const provider = {
  id: "mprov_test",
  name: "My endpoint",
  type: "openai",
  enabled: true,
  workspace_id: "ws_test",
  config: { base_url: "https://example.com/v1" },
};
const definition = {
  type: "openai",
  display_name: "OpenAI",
  model_apis: ["openai.chat_completions", "openai.responses"],
  default_model_api: "openai.chat_completions",
  model_api_labels: {
    "openai.chat_completions": "Chat Completions",
    "openai.responses": "Responses",
  },
  catalog_providers: ["openai"],
  supports_test: true,
  setup_url: null,
  setup_label: null,
  authentication: { mode: "required" },
  credential_schema: {
    type: "string",
    "x-a13n-credential-format": "api_key",
  },
  configuration_schema: {
    type: "object",
    properties: { base_url: { type: "string" }, auth_mode: { type: "string" } },
  },
};
const pricing = (model: string, input: string, output: string) => ({
  provider: "openai",
  model,
  source: "genai_prices",
  source_revision: "2026-09-01",
  rules: [
    {
      rule_id: "standard",
      constraint: { kind: "always" },
      prices: [
        { price_key: "input_mtok", price: input },
        { price_key: "output_mtok", price: output },
      ],
    },
  ],
});
const entry = {
  identity: "openai/gpt-5.5",
  ref: { provider: "openai", model: "gpt-5.5" },
  name: "GPT-5.5",
  provider_name: "OpenAI",
  release_date: "2026-04-23",
  characteristics: {
    capabilities: ["image_understanding"],
    context_window_tokens: 100000,
  },
  pricing: pricing("gpt-5.5", "5", "30"),
  pricing_warning: null,
};
const secondEntry = {
  ...entry,
  identity: "openai/gpt-5.6",
  ref: { provider: "openai", model: "gpt-5.6" },
  name: "GPT-5.6",
  characteristics: { capabilities: [], context_window_tokens: 200000 },
  pricing: pricing("gpt-5.6", "4", "24"),
};
const compatibleEntry = {
  identity: "minimax/MiniMax-M3",
  ref: { provider: "minimax", model: "MiniMax-M3" },
  name: "MiniMax-M3",
  provider_name: "MiniMax",
  release_date: "2026-06-01",
  characteristics: {},
  pricing: { ...pricing("MiniMax-M3", "0.5", "2"), provider: "minimax" },
  pricing_warning: null,
};
const model = {
  workspace_id: "ws_test",
  provider_id: "mprov_test",
  key: "smart",
  name: "Smart",
  description: "Company gateway model",
  config: {
    model_name: "company-smart",
    model_api: "openai.responses",
    characteristics: { capabilities: ["image_understanding"] },
    max_tokens: 4096,
    extra_body: { reasoning: { effort: "future" } },
    extra_headers: { "x-experiment": "candidate" },
  },
  pricing: pricing("company-smart", "1", "2"),
  catalog_ref: { provider: "openai", model: "gpt-5.5" },
  enabled: true,
  version: 3,
};
const response = () => new Response(null, { headers: { ETag: '"smart:3"' } });
function mount(ids: { providerId?: string; modelKey?: string } = {}) {
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false, gcTime: 0 } },
        })
      }
    >
      <MemoryRouter>
        <ModelEditor {...ids} controlledOpen onClose={state.close} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
const modelsPath = "/api/v1/models";

beforeEach(() => {
  state.catalog = {
    items: [entry, secondEntry, compatibleEntry],
    status: "ready",
  };
  state.types = [definition];
  state.providers = [provider];
  state.GET.mockImplementation(async (path: string) => ({
    data:
      path === "/api/v1/provider-types/{kind}"
        ? { items: state.types, next_cursor: null }
        : path === "/api/v1/model-catalog"
          ? state.catalog
          : path.endsWith("{key}")
            ? model
            : { items: state.providers, next_cursor: null },
    response: response(),
  }));
  state.POST.mockImplementation(
    async (_path: string, args: { body?: unknown }) => ({
      data: { ...(args.body as object) },
      response: response(),
    }),
  );
  state.PATCH.mockResolvedValue({ data: model, response: response() });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  HTMLElement.prototype.hasPointerCapture = () => false;
  HTMLElement.prototype.setPointerCapture = () => {};
  HTMLElement.prototype.releasePointerCapture = () => {};
  HTMLElement.prototype.scrollIntoView = () => {};
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.resetAllMocks();
});

it("creates a manual model with JSON request defaults in its configuration", async () => {
  mount({ providerId: "mprov_test" });
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /Custom model/ }));
  await user.type(
    await screen.findByLabelText("Upstream model"),
    "company-smart",
  );
  await user.type(screen.getByLabelText("Name"), "Smart");
  await user.type(screen.getByLabelText("Model key"), "smart");
  fireEvent.change(screen.getByLabelText("Description"), {
    target: { value: "Company gateway" },
  });
  await user.click(screen.getByRole("switch", { name: "Enabled" }));
  expect(screen.queryByLabelText("Thinking effort")).toBeNull();
  expect(screen.queryByLabelText("Max output tokens")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Advanced" }));
  fireEvent.change(screen.getByLabelText("Settings JSON"), {
    target: { value: '{"max_tokens":4096,"temperature":0.2}' },
  });
  await user.click(screen.getByRole("button", { name: "Add model" }));
  await waitFor(() =>
    expect(state.POST).toHaveBeenCalledWith(modelsPath, {
      body: {
        provider_id: "mprov_test",
        key: "smart",
        name: "Smart",
        description: "Company gateway",
        enabled: false,
        config: {
          max_tokens: 4096,
          temperature: 0.2,
          model_name: "company-smart",
          model_api: "openai.chat_completions",
          characteristics: {},
        },
        pricing: null,
        catalog_ref: null,
      },
    }),
  );
});

it("rejects request defaults that are not a JSON object", async () => {
  mount({ providerId: "mprov_test" });
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /Custom model/ }));
  await user.type(
    await screen.findByLabelText("Upstream model"),
    "company-smart",
  );
  await user.type(screen.getByLabelText("Name"), "Smart");
  await user.type(screen.getByLabelText("Model key"), "smart");
  await user.click(screen.getByRole("button", { name: "Advanced" }));
  fireEvent.change(screen.getByLabelText("Settings JSON"), {
    target: { value: "[4096]" },
  });
  await user.click(screen.getByRole("button", { name: "Add model" }));
  expect(await screen.findByText("Enter a JSON object.")).toBeTruthy();
  expect(state.POST).not.toHaveBeenCalled();
});

it("keeps the catalog identity and price for an edited gateway upstream ID", async () => {
  mount({ providerId: "mprov_test" });
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /GPT-5\.5/ }));
  await user.clear(await screen.findByLabelText("Upstream model"));
  await user.type(screen.getByLabelText("Upstream model"), "company-smart");
  await user.click(screen.getByRole("button", { name: "Add model" }));
  await waitFor(() =>
    expect(state.POST).toHaveBeenCalledWith(
      modelsPath,
      expect.objectContaining({
        body: expect.objectContaining({
          key: null,
          name: "GPT-5.5",
          config: expect.objectContaining({
            model_name: "company-smart",
            characteristics: entry.characteristics,
          }),
          pricing: entry.pricing,
          catalog_ref: entry.ref,
        }),
      }),
    ),
  );
});

it("applies a newly selected model immediately, including its catalog values", async () => {
  mount({ providerId: "mprov_test" });
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /GPT-5\.5/ }));
  await user.click(
    await screen.findByRole("button", { name: "Choose a model" }),
  );
  await user.click(await screen.findByRole("button", { name: /GPT-5\.6/ }));
  expect(
    (screen.getByLabelText("Upstream model") as HTMLInputElement).value,
  ).toBe("gpt-5.6");
  expect(
    (screen.getByLabelText("Context window") as HTMLInputElement).value,
  ).toBe("200000");
  await user.click(screen.getByRole("button", { name: "Add model" }));
  await waitFor(() =>
    expect(state.POST).toHaveBeenCalledWith(modelsPath, {
      body: {
        provider_id: "mprov_test",
        key: null,
        name: "GPT-5.5",
        description: "",
        enabled: true,
        config: {
          model_name: "gpt-5.6",
          model_api: "openai.chat_completions",
          characteristics: secondEntry.characteristics,
        },
        pricing: secondEntry.pricing,
        catalog_ref: secondEntry.ref,
      },
    }),
  );
});

it("uses the official model price for an OpenAI-compatible connection", async () => {
  mount({ providerId: "mprov_test" });
  const user = userEvent.setup();
  expect(await screen.findByRole("button", { name: /GPT-5\.5/ })).toBeTruthy();
  expect(screen.queryByRole("button", { name: /MiniMax-M3/ })).toBeNull();
  await user.click(
    screen.getByRole("button", { name: "Other models (compatible)…" }),
  );
  await user.click(await screen.findByRole("button", { name: /MiniMax-M3/ }));
  expect(
    (screen.getByLabelText("Upstream model") as HTMLInputElement).value,
  ).toBe("");
  expect(
    screen.getByText(
      "Requires an OpenAI-compatible endpoint serving this model. Enter its upstream model ID.",
    ),
  ).toBeTruthy();
  expect(
    screen.getByRole("button", { name: /Pricing/ }).textContent,
  ).not.toContain("Unknown");
  await user.type(
    screen.getByLabelText("Upstream model"),
    "gateway-minimax-m3",
  );
  await user.click(screen.getByRole("button", { name: "Add model" }));
  await waitFor(() =>
    expect(state.POST).toHaveBeenCalledWith(
      modelsPath,
      expect.objectContaining({
        body: expect.objectContaining({
          key: null,
          config: expect.objectContaining({
            model_name: "gateway-minimax-m3",
            model_api: "openai.chat_completions",
          }),
          pricing: compatibleEntry.pricing,
          catalog_ref: compatibleEntry.ref,
        }),
      }),
    ),
  );
});

it("falls back to the official price when the selected channel has none", async () => {
  const routedEntry = {
    ...compatibleEntry,
    ref: { provider: "openrouter", model: "minimax/MiniMax-M3" },
    provider_name: "OpenRouter",
    pricing: null,
  };
  state.catalog = { items: [compatibleEntry, routedEntry], status: "ready" };
  state.types = [
    {
      ...definition,
      type: "openrouter",
      display_name: "OpenRouter",
      model_apis: ["openrouter.chat_completions"],
      default_model_api: "openrouter.chat_completions",
      catalog_providers: ["openrouter"],
    },
  ];
  state.providers = [{ ...provider, type: "openrouter", config: {} }];
  mount({ providerId: "mprov_test" });
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /MiniMax-M3/ }));
  expect(
    (screen.getByLabelText("Upstream model") as HTMLInputElement).value,
  ).toBe("minimax/MiniMax-M3");
  await user.click(screen.getByRole("button", { name: "Add model" }));
  await waitFor(() =>
    expect(state.POST).toHaveBeenCalledWith(
      modelsPath,
      expect.objectContaining({
        body: expect.objectContaining({
          config: expect.objectContaining({
            model_name: "minimax/MiniMax-M3",
            model_api: "openrouter.chat_completions",
          }),
          pricing: compatibleEntry.pricing,
          catalog_ref: routedEntry.ref,
        }),
      }),
    ),
  );
});

it("says when the catalog is stale and why a catalog price is missing", async () => {
  state.catalog = {
    items: [
      {
        ...entry,
        pricing: null,
        pricing_warning:
          "Unsupported catalog pricing; configure prices manually",
      },
    ],
    status: "stale",
  };
  mount({ providerId: "mprov_test" });
  const user = userEvent.setup();
  expect(
    await screen.findByText("Showing the last available model catalog."),
  ).toBeTruthy();
  await user.click(await screen.findByRole("button", { name: /GPT-5\.5/ }));
  expect(
    screen.getByText("Unsupported catalog pricing; configure prices manually"),
  ).toBeTruthy();
});

it("lets the reader enter a model ID when the catalog is unavailable", async () => {
  state.catalog = { items: [], status: "unavailable" };
  mount({ providerId: "mprov_test" });
  expect(
    await screen.findByText(
      "Catalog unavailable. Enter a model ID to continue.",
    ),
  ).toBeTruthy();
  await userEvent
    .setup()
    .click(screen.getByRole("button", { name: /Custom model/ }));
  expect(await screen.findByLabelText("Upstream model")).toBeTruthy();
});

it("saves an edited model under its ETag without offering a billable test", async () => {
  mount({ modelKey: "smart" });
  const user = userEvent.setup();
  const name = await screen.findByLabelText("Name");
  expect(screen.queryByRole("button", { name: "Check connection" })).toBeNull();
  expect(
    (screen.getByLabelText("Upstream model") as HTMLInputElement).value,
  ).toBe("company-smart");
  expect(
    (await screen.findByRole("combobox", { name: "API" })).textContent,
  ).toBe("Responses");
  await user.clear(name);
  await user.type(name, "Smarter");
  await user.click(screen.getByRole("switch", { name: "Enabled" }));
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(state.PATCH).toHaveBeenCalledWith(`${modelsPath}/{key}`, {
      params: {
        path: { key: "smart" },
      },
      headers: { "If-Match": '"smart:3"' },
      body: {
        name: "Smarter",
        description: "Company gateway model",
        enabled: false,
        config: {
          max_tokens: 4096,
          extra_body: model.config.extra_body,
          extra_headers: model.config.extra_headers,
          model_name: "company-smart",
          model_api: "openai.responses",
          characteristics: { capabilities: ["image_understanding"] },
        },
        pricing: model.pricing,
        catalog_ref: model.catalog_ref,
      },
    }),
  );
});

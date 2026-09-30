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
  settings_schemas: {
    "openai.responses": {
      type: "object",
      additionalProperties: false,
      properties: {
        thinking: {
          anyOf: [{ type: "boolean" }, { enum: ["low", "medium", "high"] }],
        },
        max_tokens: { type: "integer", minimum: 1 },
        temperature: { type: "number" },
        openai_store: { anyOf: [{ type: "boolean" }, { type: "null" }] },
        openai_reasoning_summary: { enum: ["auto", "concise", "detailed"] },
        openai_text_verbosity: { enum: ["low", "medium", "high"] },
        extra_body: { type: "object" },
        extra_headers: { type: "object" },
      },
    },
  },
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
          settings: { max_tokens: 4096, temperature: 0.2 },
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
          settings: {},
        },
        pricing: secondEntry.pricing,
        catalog_ref: secondEntry.ref,
      },
    }),
  );
});

it("seeds image input from each selected catalog model instead of leaking the previous draft", async () => {
  state.catalog = {
    items: [
      {
        ...entry,
        characteristics: { ...entry.characteristics, image_input: null },
      },
      {
        ...secondEntry,
        characteristics: {
          ...secondEntry.characteristics,
          image_input: {
            support_gif: false,
            max_images: 9,
            max_image_bytes: 2621440,
          },
        },
      },
    ],
    status: "ready",
  };
  mount({ providerId: "mprov_test" });
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /GPT-5\.5/ }));
  await user.click(screen.getByRole("button", { name: /^Advanced/ }));
  expect(
    screen
      .getByRole("switch", { name: "Prepare images" })
      .getAttribute("aria-checked"),
  ).toBe("false");
  await user.click(screen.getByRole("button", { name: "Choose a model" }));
  await user.click(await screen.findByRole("button", { name: /GPT-5\.6/ }));
  expect(
    screen
      .getByRole("switch", { name: "Prepare images" })
      .getAttribute("aria-checked"),
  ).toBe("true");
  expect(
    screen
      .getByRole("switch", { name: "Support GIF" })
      .getAttribute("aria-checked"),
  ).toBe("false");
  expect((screen.getByLabelText("Max images") as HTMLInputElement).value).toBe(
    "9",
  );
  expect(
    (screen.getByLabelText("Max image size (MiB)") as HTMLInputElement).value,
  ).toBe("2.5");
  await user.click(screen.getByRole("button", { name: "Add model" }));
  await waitFor(() => expect(state.POST).toHaveBeenCalled());
  expect(
    state.POST.mock.calls[0][1].body.config.characteristics.image_input,
  ).toEqual({ support_gif: false, max_images: 9, max_image_bytes: 2621440 });
});

it("resets image input when changing providers and omits the untouched default policy", async () => {
  state.providers = [
    provider,
    { ...provider, id: "mprov_second", name: "Second endpoint" },
  ];
  mount();
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /My endpoint/ }));
  await user.click(await screen.findByRole("button", { name: /Custom model/ }));
  await user.click(screen.getByRole("button", { name: /^Advanced/ }));
  await user.click(screen.getByRole("switch", { name: "Prepare images" }));
  await user.click(screen.getByRole("button", { name: "Choose a model" }));
  await user.click(screen.getByRole("button", { name: "Choose a provider" }));
  await user.click(screen.getByRole("button", { name: /Second endpoint/ }));
  await user.click(await screen.findByRole("button", { name: /Custom model/ }));
  expect(
    screen
      .getByRole("switch", { name: "Prepare images" })
      .getAttribute("aria-checked"),
  ).toBe("true");
  expect((screen.getByLabelText("Max images") as HTMLInputElement).value).toBe(
    "20",
  );
  await user.type(screen.getByLabelText("Upstream model"), "company-smart");
  await user.type(screen.getByLabelText("Name"), "Smart");
  await user.click(screen.getByRole("button", { name: "Add model" }));
  await waitFor(() => expect(state.POST).toHaveBeenCalled());
  expect(state.POST.mock.calls[0][1].body.provider_id).toBe("mprov_second");
  expect(state.POST.mock.calls[0][1].body.config.characteristics).toEqual({});
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
          settings: {
            max_tokens: 4096,
            extra_body: model.config.extra_body,
            extra_headers: model.config.extra_headers,
          },
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

it("edits native defaults through fields and JSON without losing other settings", async () => {
  mount({ modelKey: "smart" });
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /^Advanced/ }));
  expect(
    screen.getByRole("combobox", { name: "Store response" }).textContent,
  ).toBe("Default (off)");
  expect(screen.queryByLabelText("Temperature")).toBeNull();
  expect(screen.queryByLabelText("Top P")).toBeNull();
  const native = {
    openai_store: true,
    thinking: "low",
    openai_text_verbosity: "low",
    extra_headers: { "x-existing": "keep" },
  };
  fireEvent.change(screen.getByLabelText("Settings JSON"), {
    target: { value: JSON.stringify(native) },
  });
  expect(
    screen.getByRole("combobox", { name: "Store response" }).textContent,
  ).toBe("On");
  expect(
    screen.getByRole("combobox", { name: "Thinking effort" }).textContent,
  ).toBe("Low");
  await user.click(screen.getByRole("combobox", { name: "Thinking effort" }));
  await user.click(await screen.findByRole("option", { name: "Medium" }));
  await user.click(screen.getByRole("combobox", { name: "Reasoning summary" }));
  await user.click(await screen.findByRole("option", { name: "Detailed" }));
  fireEvent.change(screen.getByLabelText("Max output tokens"), {
    target: { value: "8192" },
  });
  await user.click(screen.getByRole("combobox", { name: "Store response" }));
  await user.click(
    await screen.findByRole("option", { name: "Default (off)" }),
  );
  const { openai_store: _store, ...preserved } = native;
  const expected = {
    ...preserved,
    thinking: "medium",
    openai_reasoning_summary: "detailed",
    max_tokens: 8192,
  };
  expect(
    JSON.parse(
      (screen.getByLabelText("Settings JSON") as HTMLTextAreaElement).value,
    ),
  ).toEqual(expected);
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(state.PATCH).toHaveBeenCalledWith(
      `${modelsPath}/{key}`,
      expect.objectContaining({
        body: expect.objectContaining({
          config: expect.objectContaining({ settings: expected }),
        }),
      }),
    ),
  );
});

it("preserves stored native settings when only the name is edited", async () => {
  const settings = {
    openai_store: false,
    thinking: "high",
    openai_text_verbosity: "low",
    extra_headers: {},
  };
  const originalGet = state.GET.getMockImplementation()!;
  state.GET.mockImplementation(async (path: string, args: unknown) =>
    path.endsWith("{key}")
      ? {
          data: { ...model, config: { ...model.config, settings } },
          response: response(),
        }
      : originalGet(path, args),
  );
  mount({ modelKey: "smart" });
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Name"), " updated");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(state.PATCH).toHaveBeenCalledWith(
      `${modelsPath}/{key}`,
      expect.objectContaining({
        body: expect.objectContaining({
          config: expect.objectContaining({
            settings: {
              max_tokens: 4096,
              extra_body: model.config.extra_body,
              ...settings,
            },
          }),
        }),
      }),
    ),
  );
});

it("keeps invalid JSON editable and rejects settings for a different API", async () => {
  mount({ modelKey: "smart" });
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /^Advanced/ }));
  const json = screen.getByLabelText("Settings JSON");
  fireEvent.change(json, { target: { value: "{" } });
  expect(
    (screen.getByLabelText("Max output tokens") as HTMLInputElement).disabled,
  ).toBe(true);
  expect(
    screen.getByText("Fix the settings JSON to use the fields above."),
  ).toBeTruthy();
  fireEvent.change(json, { target: { value: '{"anthropic_effort":"high"}' } });
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(
    await screen.findByText(/must NOT have additional properties/),
  ).toBeTruthy();
  expect(state.PATCH).not.toHaveBeenCalled();
});

it("edits image input controls while preserving exact bytes, hidden policy and other traits", async () => {
  const saved = {
    ...model,
    config: {
      ...model.config,
      characteristics: {
        ...model.config.characteristics,
        compact_threshold: 0.75,
        image_input: {
          max_image_bytes: 1234567,
          max_image_dimension: 3456,
          split_large_images: false,
          image_split_max_height: 2222,
          image_split_overlap: 11,
        },
      },
    },
  };
  state.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("{key}")
      ? saved
      : path === "/api/v1/provider-types/{kind}"
        ? { items: state.types }
        : path === "/api/v1/model-catalog"
          ? state.catalog
          : { items: state.providers },
    response: response(),
  }));
  mount({ modelKey: "smart" });
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /^Advanced/ }));
  expect(
    screen
      .getByRole("switch", { name: "Prepare images" })
      .getAttribute("aria-checked"),
  ).toBe("true");
  expect(
    (screen.getByLabelText("Max image size (MiB)") as HTMLInputElement).value,
  ).toBe(String(1234567 / 1048576));
  await user.click(screen.getByRole("switch", { name: "Support GIF" }));
  fireEvent.change(screen.getByLabelText("Max images"), {
    target: { value: "7" },
  });
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(state.PATCH).toHaveBeenCalled());
  expect(state.PATCH.mock.calls[0][1].body.config.characteristics).toEqual({
    ...saved.config.characteristics,
    image_input: {
      ...saved.config.characteristics.image_input,
      support_gif: false,
      max_images: 7,
    },
  });
});

it("saves disabled preparation as null and keeps it disabled after reopening", async () => {
  mount({ modelKey: "smart" });
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /^Advanced/ }));
  await user.click(screen.getByRole("switch", { name: "Prepare images" }));
  expect(screen.queryByLabelText("Max images")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(state.PATCH).toHaveBeenCalled());
  const saved = { ...model, config: state.PATCH.mock.calls[0][1].body.config };
  expect(saved.config.characteristics.image_input).toBeNull();
  cleanup();
  state.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("{key}")
      ? saved
      : path === "/api/v1/provider-types/{kind}"
        ? { items: state.types }
        : path === "/api/v1/model-catalog"
          ? state.catalog
          : { items: state.providers },
    response: response(),
  }));
  mount({ modelKey: "smart" });
  await user.click(await screen.findByRole("button", { name: /^Advanced/ }));
  expect(
    screen
      .getByRole("switch", { name: "Prepare images" })
      .getAttribute("aria-checked"),
  ).toBe("false");
});

it("keeps invalid image input visible and does not submit it", async () => {
  mount({ modelKey: "smart" });
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /^Advanced/ }));
  fireEvent.change(screen.getByLabelText("Max images"), {
    target: { value: "1.5" },
  });
  expect(
    screen.getByText("Enter a whole number of images, zero or greater."),
  ).not.toBeNull();
  // Submit directly too: request validation must not rely only on HTML constraints.
  fireEvent.submit(
    screen.getByRole("button", { name: "Save changes" }).closest("form")!,
  );
  await waitFor(() =>
    expect(
      screen.getAllByText("Enter a whole number of images, zero or greater.")
        .length,
    ).toBe(2),
  );
  expect(state.PATCH).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Max images"), {
    target: { value: "20" },
  });
  fireEvent.change(screen.getByLabelText("Max image size (MiB)"), {
    target: { value: "2.5" },
  });
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(state.PATCH).toHaveBeenCalled());
  expect(
    state.PATCH.mock.calls[0][1].body.config.characteristics.image_input
      .max_image_bytes,
  ).toBe(2621440);
});

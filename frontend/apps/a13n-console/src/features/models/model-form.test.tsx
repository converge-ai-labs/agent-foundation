import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ModelForm } from "./model-form";

const state = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  PATCH: vi.fn(),
  close: vi.fn(),
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({
    http: { GET: state.GET, POST: state.POST, PATCH: state.PATCH },
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("../../layout/workspace", () => ({
  useAccess: () => ({ workspace: { key: "workspace-test" } }),
}));
const provider = {
  id: "mp_test",
  name: "My endpoint",
  type: "openai",
  enabled: true,
  configuration: { base_url: "https://example.com/v1" },
};
const settingsSchema = {
  type: "object",
  additionalProperties: false,
  properties: {
    temperature: {
      type: "number",
      description: "A very long parameter explanation.",
    },
    max_tokens: { type: "integer" },
    thinking: { type: "string", enum: ["low", "high"] },
  },
};
const definition = {
  type: "openai",
  display_name: "OpenAI",
  default_model_api: "openai.chat_completions",
  supported_model_apis: ["openai.chat_completions", "openai.responses"],
  catalog_providers: ["openai"],
  model_api_labels: {
    "openai.chat_completions": "OpenAI Chat Completions",
    "openai.responses": "OpenAI Responses",
  },
  settings_schemas: {
    "openai.chat_completions": settingsSchema,
    "openai.responses": settingsSchema,
  },
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
function mount(
  providerId?: string,
  resource?: Parameters<typeof ModelForm>[0]["resource"],
) {
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false, gcTime: 0 } },
        })
      }
    >
      <ModelForm
        scope={{ kind: "workspace", id: "ws_test" }}
        providerId={providerId}
        resource={resource}
        close={state.close}
        reload={async () => {}}
      />
    </QueryClientProvider>,
  );
}

const entry = {
  identity: "openai/gpt-5.5",
  ref: { provider: "openai", model: "gpt-5.5" },
  name: "GPT-5.5",
  provider_name: "OpenAI",
  release_date: "2026-04-23",
  declarations: {
    capabilities: ["image_understanding"],
    context_window_tokens: 100000,
    pricing: { tiers: [{ above: null, rates: { input: "5", output: "30" } }] },
  },
};
const secondEntry = {
  ...entry,
  identity: "openai/gpt-5.6",
  ref: { provider: "openai", model: "gpt-5.6" },
  name: "GPT-5.6",
  declarations: {
    capabilities: [],
    context_window_tokens: 200000,
    pricing: { tiers: [{ above: null, rates: { input: "4", output: "24" } }] },
  },
};
const compatibleEntry = {
  identity: "minimax/MiniMax-M3",
  ref: { provider: "minimax", model: "MiniMax-M3" },
  name: "MiniMax-M3",
  provider_name: "MiniMax",
  release_date: "2026-06-01",
  declarations: {
    pricing: { tiers: [{ above: null, rates: { input: "0.5", output: "2" } }] },
  },
};
beforeEach(() => {
  state.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("model-catalog")
      ? {
          items: [entry, secondEntry, compatibleEntry],
          status: "ready",
          released_since: "2026-04-23",
        }
      : {
          items: path.endsWith("model-provider-types")
            ? [definition]
            : [provider],
          next_cursor: null,
        },
  }));
  state.POST.mockImplementation(
    async (_path: string, args: { body?: unknown }) => ({
      data: { id: "mdl_test", ...(args.body as object) },
    }),
  );
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  HTMLElement.prototype.scrollIntoView = () => {};
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.resetAllMocks();
});

it("creates a manual model with JSON-only request settings", async () => {
  mount("mp_test");
  const user = userEvent.setup();
  await user.type(
    await screen.findByLabelText("Upstream model"),
    "company-smart",
  );
  await user.type(screen.getByLabelText("Name"), "Smart");
  await user.type(screen.getByLabelText("Model key"), "smart");
  expect(screen.queryByLabelText("Thinking effort")).toBeNull();
  expect(screen.queryByLabelText("Max output tokens")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Request settings" }));
  fireEvent.change(screen.getByLabelText("Settings JSON"), {
    target: { value: '{"thinking":"high","max_tokens":4096}' },
  });
  await user.click(screen.getByRole("button", { name: "Add model" }));
  await waitFor(() =>
    expect(state.POST).toHaveBeenCalledWith(
      "/api/v1/workspaces/{workspace}/models",
      expect.objectContaining({
        body: expect.objectContaining({
          upstream_model: "company-smart",
          catalog_ref: null,
          settings: { thinking: "high", max_tokens: 4096 },
        }),
      }),
    ),
  );
  expect(
    state.POST.mock.calls.some(([path]) => String(path).includes("discover")),
  ).toBe(false);
});

it("rejects request settings that do not match the selected API", async () => {
  mount("mp_test");
  const user = userEvent.setup();
  await user.type(
    await screen.findByLabelText("Upstream model"),
    "company-smart",
  );
  await user.type(screen.getByLabelText("Name"), "Smart");
  await user.type(screen.getByLabelText("Model key"), "smart");
  await user.click(screen.getByRole("button", { name: "Request settings" }));
  fireEvent.change(screen.getByLabelText("Settings JSON"), {
    target: { value: '{"max_tokens":"many"}' },
  });
  await user.click(screen.getByRole("button", { name: "Add model" }));
  expect(state.POST).not.toHaveBeenCalled();
  expect(
    await screen.findByText("settings/max_tokens must be integer"),
  ).toBeTruthy();
});

it("keeps the catalog identity when the gateway upstream ID is edited", async () => {
  mount("mp_test");
  const user = userEvent.setup();
  await user.click(await screen.findByRole("combobox", { name: "Model" }));
  await user.click(await screen.findByRole("option", { name: /GPT-5.5/ }));
  await user.clear(screen.getByLabelText("Upstream model"));
  await user.type(screen.getByLabelText("Upstream model"), "company-smart");
  await user.click(screen.getByRole("button", { name: "Add model" }));
  await waitFor(() =>
    expect(state.POST).toHaveBeenCalledWith(
      "/api/v1/workspaces/{workspace}/models",
      expect.objectContaining({
        body: expect.objectContaining({
          upstream_model: "company-smart",
          catalog_ref: entry.ref,
          declarations: expect.objectContaining({
            pricing: entry.declarations.pricing,
            context_window_tokens: 100000,
          }),
        }),
      }),
    ),
  );
});

it("applies a newly selected model immediately, including its catalog values", async () => {
  mount("mp_test");
  const user = userEvent.setup();
  await user.click(await screen.findByRole("combobox", { name: "Model" }));
  await user.click(await screen.findByRole("option", { name: /GPT-5.5/ }));
  await waitFor(() =>
    expect(screen.queryByRole("dialog", { name: "Model" })).toBeNull(),
  );
  await user.click(screen.getByRole("combobox", { name: "Model" }));
  await user.click(await screen.findByRole("option", { name: /GPT-5.6/ }));
  expect(screen.queryByText("Apply catalog values")).toBeNull();
  expect(
    (screen.getByLabelText("Upstream model") as HTMLInputElement).value,
  ).toBe("gpt-5.6");
  expect(
    (screen.getByLabelText("Context window") as HTMLInputElement).value,
  ).toBe("200000");
  await user.click(screen.getByRole("button", { name: "Add model" }));
  await waitFor(() =>
    expect(state.POST).toHaveBeenCalledWith(
      "/api/v1/workspaces/{workspace}/models",
      expect.objectContaining({
        body: expect.objectContaining({
          catalog_ref: secondEntry.ref,
          upstream_model: "gpt-5.6",
          declarations: expect.objectContaining({
            pricing: secondEntry.declarations.pricing,
          }),
        }),
      }),
    ),
  );
});

it("uses the official model price for an OpenAI-compatible connection", async () => {
  mount("mp_test");
  const user = userEvent.setup();
  await user.click(await screen.findByRole("combobox", { name: "Model" }));
  await user.keyboard("{ArrowDown}");
  await user.click(
    screen.getByRole("button", { name: "Other models (compatible)…" }),
  );
  await user.click(await screen.findByRole("option", { name: "MiniMax-M3" }));
  expect(
    (screen.getByLabelText("Upstream model") as HTMLInputElement).value,
  ).toBe("");
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
      "/api/v1/workspaces/{workspace}/models",
      expect.objectContaining({
        body: expect.objectContaining({
          upstream_model: "gateway-minimax-m3",
          catalog_ref: compatibleEntry.ref,
          declarations: expect.objectContaining({
            pricing: compatibleEntry.declarations.pricing,
          }),
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
    declarations: { pricing: null },
  };
  state.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("model-catalog")
      ? {
          items: [compatibleEntry, routedEntry],
          status: "ready",
          released_since: "2026-04-23",
        }
      : {
          items: path.endsWith("model-provider-types")
            ? [
                {
                  ...definition,
                  type: "openrouter",
                  catalog_providers: ["openrouter"],
                },
              ]
            : [{ ...provider, type: "openrouter" }],
          next_cursor: null,
        },
  }));
  mount("mp_test");
  const user = userEvent.setup();
  await user.click(await screen.findByRole("combobox", { name: "Model" }));
  await user.click(await screen.findByRole("option", { name: "MiniMax-M3" }));
  await user.click(screen.getByRole("button", { name: "Add model" }));
  await waitFor(() =>
    expect(state.POST).toHaveBeenCalledWith(
      "/api/v1/workspaces/{workspace}/models",
      expect.objectContaining({
        body: expect.objectContaining({
          catalog_ref: routedEntry.ref,
          declarations: expect.objectContaining({
            pricing: compatibleEntry.declarations.pricing,
          }),
        }),
      }),
    ),
  );
});

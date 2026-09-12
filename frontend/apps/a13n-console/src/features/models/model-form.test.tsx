import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
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
    openai_reasoning_effort: { type: "string", enum: ["low", "high"] },
  },
};
const definition = {
  type: "openai",
  display_name: "OpenAI",
  default_model_api: "openai.chat_completions",
  supported_model_apis: ["openai.chat_completions", "openai.responses"],
  supports_model_discovery: true,
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
beforeEach(() => {
  state.GET.mockImplementation(async (path: string) => ({
    data: {
      items: path.endsWith("model-provider-types") ? [definition] : [provider],
      next_cursor: null,
    },
  }));
  state.POST.mockImplementation(
    async (path: string, args: { body?: unknown }) => {
      if (path.endsWith("discover-models"))
        return {
          data: {
            items: [
              {
                upstream_model: "vendor/model-v1",
                display_name: "Model V1",
                suggested_model_api: "openai.chat_completions",
                suggested_settings: {},
              },
            ],
            settings_schemas: {
              "openai.chat_completions": settingsSchema,
              "openai.responses": settingsSchema,
            },
          },
        };
      if (path.endsWith("describe-model"))
        return {
          data: {
            settings_schema: settingsSchema,
            parameter_support: {},
            suggested_model_api: "openai.chat_completions",
          },
        };
      if (path.endsWith("model-providers")) return { data: provider };
      return { data: { id: "mdl_test", ...(args.body as object) } };
    },
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
it.each([
  { type: "openai", path: "responses", mode: "bearer" },
  {
    type: "openai",
    path: "chat/completions",
    mode: "none",
  },
  {
    type: "openai",
    path: "responses",
    mode: "api_key_header",
  },
])(
  "connects $type with $mode using /$path and saves a Responses model",
  async ({ type, path, mode }) => {
    const selectedDefinition = definition;
    state.GET.mockImplementation(async (path: string) => ({
      data: {
        items: path.endsWith("model-provider-types")
          ? [selectedDefinition]
          : [{ ...provider, type }],
        next_cursor: null,
      },
    }));
    const previous = state.POST.getMockImplementation()!;
    state.POST.mockImplementation(async (path: string, args: unknown) => {
      if (path.endsWith("model-providers"))
        return { data: { ...provider, type } };
      return previous(path, args);
    });
    const user = userEvent.setup();
    mount();
    await user.click(
      await screen.findByRole("button", { name: "Connect provider" }),
    );
    await user.type(
      await screen.findByRole("textbox", { name: "Name" }),
      "My endpoint",
    );
    await user.click(screen.getByRole("button", { name: /Advanced settings/ }));
    await user.type(
      screen.getByRole("textbox", { name: "Base URL" }),
      `https://example.com/v1/${path}`,
    );
    await user.click(
      screen.getByRole("button", { name: "Use base URL without the API path" }),
    );
    await user.type(screen.getByLabelText("API key"), "test-key");
    if (mode !== "bearer") {
      await user.click(
        screen.getByRole("combobox", { name: "Authentication" }),
      );
      await user.click(
        await screen.findByRole("option", {
          name: mode === "none" ? "None" : "Custom header",
        }),
      );
      if (mode === "none")
        expect(screen.queryByLabelText("API key")).toBeNull();
      else
        await user.type(
          screen.getByRole("textbox", { name: "Header name" }),
          "x-model-key",
        );
    }
    await user.click(screen.getByRole("button", { name: "Connect provider" }));
    await screen.findByRole("tab", { name: "Enter model ID" });
    expect(state.POST).toHaveBeenCalledWith(
      "/api/v1/workspaces/{workspace}/model-providers",
      expect.objectContaining({
        body: expect.objectContaining({
          type,
          configuration: {
            base_url: "https://example.com/v1",
            ...(mode !== "bearer" ? { auth_mode: mode } : {}),
            ...(mode === "api_key_header"
              ? { api_key_header_name: "x-model-key" }
              : {}),
          },
          credential: mode === "none" ? null : "test-key",
        }),
      }),
    );
    expect(screen.queryByLabelText("API key")).toBeNull();
    await user.click(screen.getByRole("tab", { name: "Enter model ID" }));
    await user.type(
      await screen.findByRole("textbox", { name: "Upstream model" }),
      "custom-model",
    );
    expect(
      (screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement).value,
    ).toBe("custom-model");
    expect(
      (screen.getByRole("textbox", { name: "Model key" }) as HTMLInputElement)
        .value,
    ).toBe("custom-model");
    await user.click(screen.getByRole("button", { name: "Add model" }));
    await waitFor(() => expect(state.close).toHaveBeenCalled());
    expect(state.POST).toHaveBeenCalledWith(
      "/api/v1/workspaces/{workspace}/models",
      expect.objectContaining({
        body: expect.objectContaining({
          provider_id: "mp_test",
          enabled: true,
          upstream_model: "custom-model",
          model_api:
            path === "responses"
              ? "openai.responses"
              : "openai.chat_completions",
          settings: {},
        }),
      }),
    );
    expect(state.POST.mock.calls.some(([path]) => path.endsWith("/test"))).toBe(
      false,
    );
  },
);
it("prefills a catalog model and preserves JSON overrides when switching APIs", async () => {
  const user = userEvent.setup();
  mount("mp_test");
  await user.click(await screen.findByRole("combobox", { name: "Model" }));
  await user.click(await screen.findByRole("option", { name: /Model V1/ }));
  expect(
    (screen.getByRole("textbox", { name: "Model key" }) as HTMLInputElement)
      .value,
  ).toBe("vendor/model-v1");
  await user.click(screen.getByRole("button", { name: /Parameters/ }));
  await screen.findByRole("spinbutton", { name: "Temperature" });
  expect(screen.queryByText("A very long parameter explanation.")).toBeNull();
  await user.click(screen.getByRole("tab", { name: "JSON" }));
  const json = await screen.findByRole("textbox", { name: "Settings JSON" });
  await user.clear(json);
  await user.paste('{"temperature":0.4}');
  await user.click(screen.getByRole("combobox", { name: "API" }));
  await user.click(await screen.findByRole("option", { name: "Responses" }));
  expect(
    (
      screen.getByRole("textbox", {
        name: "Settings JSON",
      }) as HTMLTextAreaElement
    ).value,
  ).toBe('{"temperature":0.4}');
  await user.click(screen.getByRole("button", { name: "Add model" }));
  await waitFor(() => expect(state.close).toHaveBeenCalled());
  expect(state.POST).toHaveBeenCalledWith(
    "/api/v1/workspaces/{workspace}/models",
    expect.objectContaining({
      body: expect.objectContaining({
        model_api: "openai.responses",
        settings: { temperature: 0.4 },
      }),
    }),
  );
});
it("keeps manual entry available when the catalog fails", async () => {
  const previous = state.POST.getMockImplementation()!;
  state.POST.mockImplementation((path: string, args: unknown) =>
    path.endsWith("discover-models")
      ? Promise.reject(new Error("Catalog failed"))
      : previous(path, args),
  );
  const user = userEvent.setup();
  mount("mp_test");
  await screen.findByText("Catalog unavailable. Enter a model ID to continue.");
  await user.click(screen.getByRole("button", { name: "Enter model ID" }));
  await user.type(
    await screen.findByRole("textbox", { name: "Upstream model" }),
    "manual-model",
  );
  await user.click(screen.getByRole("button", { name: "Add model" }));
  await waitFor(() => expect(state.close).toHaveBeenCalled());
});

it("reopens parameters when invalid JSON is submitted from a collapsed section", async () => {
  const user = userEvent.setup();
  mount("mp_test");
  await user.click(await screen.findByRole("tab", { name: "Enter model ID" }));
  await user.type(
    await screen.findByRole("textbox", { name: "Upstream model" }),
    "manual-model",
  );
  await user.click(screen.getByRole("button", { name: /Parameters/ }));
  await user.click(screen.getByRole("tab", { name: "JSON" }));
  const editor = await screen.findByRole("textbox", { name: "Settings JSON" });
  await user.clear(editor);
  await user.paste("{invalid");
  await user.click(screen.getByRole("button", { name: /Parameters/ }));
  await user.click(screen.getByRole("button", { name: "Add model" }));
  await waitFor(() =>
    expect(
      screen
        .getByRole("button", { name: /Parameters/ })
        .getAttribute("aria-expanded"),
    ).toBe("true"),
  );
  expect(state.POST.mock.calls.some(([path]) => path.endsWith("/models"))).toBe(
    false,
  );
  expect(state.close).not.toHaveBeenCalled();
});
it("opens endpoint setup directly when there are no providers and allows cancellation", async () => {
  state.GET.mockImplementation(async (path: string) => ({
    data: {
      items: path.endsWith("model-provider-types") ? [definition] : [],
      next_cursor: null,
    },
  }));
  const user = userEvent.setup();
  mount();
  await user.click(
    await screen.findByRole("button", { name: /Advanced settings/ }),
  );
  await user.type(
    await screen.findByRole("textbox", { name: "Base URL" }),
    "https://models.example.com/v1",
  );
  expect(
    (screen.getByRole("textbox", { name: "Name" }) as HTMLInputElement).value,
  ).toBe("models.example.com");
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  expect(state.close).toHaveBeenCalled();
  expect(state.POST).not.toHaveBeenCalled();
});

it("keeps identity fields and actions available before model selection and preserves drafts across source tabs", async () => {
  const user = userEvent.setup();
  mount("mp_test");
  await screen.findByRole("tab", { name: "Enter model ID" });
  const name = screen.getByRole("textbox", { name: "Name" });
  const key = screen.getByRole("textbox", { name: "Model key" });
  expect(
    (screen.getByRole("button", { name: "Add model" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  await user.type(name, "Team model");
  await user.type(key, "team-model");
  await user.click(screen.getByRole("tab", { name: "Enter model ID" }));
  await user.type(
    screen.getByRole("textbox", { name: "Upstream model" }),
    "custom-model",
  );
  await user.click(screen.getByRole("tab", { name: "From catalog" }));
  expect(screen.getByRole("textbox", { name: "Name" })).toBe(name);
  expect((name as HTMLInputElement).value).toBe("Team model");
  expect((key as HTMLInputElement).value).toBe("team-model");
  await user.click(screen.getByRole("button", { name: "Cancel" }));
  expect(state.close).toHaveBeenCalled();
  expect(state.POST.mock.calls.some(([path]) => path.endsWith("/models"))).toBe(
    false,
  );
});

it("holds status edits until save and restores the unchanged state when reverted", async () => {
  const user = userEvent.setup();
  const principal = {
    principal_id: "usr_test",
    principal_type: "user" as const,
  };
  const model = {
    id: "mdl_test",
    key: "team-model",
    name: "Team model",
    provider_id: "mp_test",
    upstream_model: "custom-model",
    model_api: "openai.chat_completions",
    settings: {},
    enabled: true,
    description: null,
    organization_id: "org_test",
    workspace_id: "ws_test",
    created_at: "2026-09-12T00:00:00Z",
    updated_at: "2026-09-12T00:00:00Z",
    created_by: principal,
    updated_by: principal,
  };
  state.PATCH.mockResolvedValue({ data: { ...model, enabled: false } });
  mount(undefined, { value: model, etag: '"v1"' });
  const save = await screen.findByRole("button", { name: "Save changes" });
  await user.click(screen.getByRole("button", { name: /^Connection/ }));
  const upstream = await screen.findByRole("textbox", {
    name: "Upstream model",
  });
  await user.clear(upstream);
  await user.type(upstream, "edited-model");
  await user.click(screen.getByRole("button", { name: /^Connection/ }));
  await user.click(screen.getByRole("button", { name: /^Connection/ }));
  expect(
    (
      screen.getByRole("textbox", {
        name: "Upstream model",
      }) as HTMLInputElement
    ).value,
  ).toBe("edited-model");
  await user.clear(screen.getByRole("textbox", { name: "Upstream model" }));
  await user.type(
    screen.getByRole("textbox", { name: "Upstream model" }),
    "custom-model",
  );
  await user.click(screen.getByRole("button", { name: /^Connection/ }));
  expect((save as HTMLButtonElement).disabled).toBe(true);
  await user.click(
    screen.getByRole("switch", { name: /^(Enabled|Disabled)$/ }),
  );
  expect((save as HTMLButtonElement).disabled).toBe(false);
  expect(state.PATCH).not.toHaveBeenCalled();
  await user.click(
    screen.getByRole("switch", { name: /^(Enabled|Disabled)$/ }),
  );
  expect((save as HTMLButtonElement).disabled).toBe(true);
  await user.click(
    screen.getByRole("switch", { name: /^(Enabled|Disabled)$/ }),
  );
  await user.click(save);
  await waitFor(() => expect(state.close).toHaveBeenCalled());
  expect(state.PATCH).toHaveBeenCalledWith(
    "/api/v1/workspaces/{workspace}/models/{model_id}",
    expect.objectContaining({
      body: expect.objectContaining({ enabled: false, name: "Team model" }),
    }),
  );
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { ProviderForm } from "./provider-form";

const state = vi.hoisted(() => ({ PATCH: vi.fn(), close: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({
    http: { PATCH: state.PATCH },
    workspace: () => ({ PATCH: state.PATCH }),
  }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    organization: { id: "org_test" },
    workspace: { id: "ws_test" },
    can: () => true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
const provider: Schema["Provider"] = {
  id: "mprov_test",
  organization_id: "org_test",
  workspace_id: "ws_test",
  type: "deepseek",
  name: "DeepSeek Gateway",
  config: {
    base_url: "https://gateway.example/v1",
  },
  credential_configured: true,
  header_names: ["x-gateway"],
  enabled: true,
  version: 1,
  created_by_id: "usr_test",
  updated_by_id: "usr_test",
  created_at: "2026-09-11T00:00:00Z",
  updated_at: "2026-09-11T00:00:00Z",
};
function mount() {
  state.PATCH.mockResolvedValue({ data: provider });
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { mutations: { retry: false } } })
      }
    >
      <ProviderForm
        resource={{ value: provider, etag: '"test"' }}
        definitions={[
          {
            type: "deepseek",
            display_name: "DeepSeek",
            supports_test: true,
            model_apis: ["openai.chat_completions"],
            default_model_api: "openai.chat_completions",
            model_api_labels: {
              "openai.chat_completions": "Chat Completions",
            },
            setup_url: null,
            setup_label: null,
            authentication: { mode: "required" },
            credential_schema: { type: "string" },
            configuration_schema: {
              type: "object",
              additionalProperties: false,
              properties: {
                base_url: { type: "string" },
                session_affinity_header: {
                  anyOf: [
                    {
                      type: "string",
                      "x-session-affinity-presets": [
                        {
                          label: "LiteLLM",
                          header: "x-litellm-session-id",
                          description: "Requires gateway configuration.",
                        },
                      ],
                    },
                    { type: "null" },
                  ],
                },
              },
            },
          },
        ]}
        close={state.close}
        reload={async () => {}}
      />
    </QueryClientProvider>,
  );
}
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it("starts collapsed and saves header rotation without resubmitting the primary credential", async () => {
  mount();
  const user = userEvent.setup();
  expect(
    screen
      .getByRole("button", { name: /Advanced settings/ })
      .getAttribute("aria-expanded"),
  ).toBe("false");
  expect(screen.queryByLabelText("Base URL")).toBeNull();
  await user.click(screen.getByRole("button", { name: /Advanced settings/ }));
  expect(
    (screen.getByLabelText("Header value 1") as HTMLInputElement).value,
  ).toBe("");
  await user.type(screen.getByLabelText("Header value 1"), "replacement");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(state.close).toHaveBeenCalled());
  const [path, request] = state.PATCH.mock.calls[0];
  expect(path).toBe("/api/v1/model-providers/{provider_id}");
  expect(request.params.path).toEqual({
    provider_id: provider.id,
  });
  expect(request.headers).toEqual({ "If-Match": '"test"' });
  expect(request.body.extra_headers).toEqual({ "x-gateway": "replacement" });
  expect(request.body.config).toEqual(provider.config);
  expect(request.body).not.toHaveProperty("credential");
});

it("reopens advanced settings for an invalid renamed secret and retains the draft", async () => {
  mount();
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /Advanced settings/ }));
  await user.clear(screen.getByLabelText("Header name 1"));
  await user.type(screen.getByLabelText("Header name 1"), "x-renamed");
  await user.click(screen.getByRole("button", { name: /Advanced settings/ }));
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByText("Enter a value for each new header.");
  expect(
    (screen.getByLabelText("Header name 1") as HTMLInputElement).value,
  ).toBe("x-renamed");
  expect(state.PATCH).not.toHaveBeenCalled();
});

it("stages header deletion with the trash button until save", async () => {
  mount();
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /Advanced settings/ }));
  expect(screen.queryByRole("checkbox", { name: "Secret value" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Remove header 1" }));
  expect(state.PATCH).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(state.close).toHaveBeenCalled());
  expect(state.PATCH.mock.calls[0][1].body.extra_headers).toEqual({
    "x-gateway": null,
  });
});

it("fills a preset, replaces it with a custom name and saves only the header name", async () => {
  mount();
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /Advanced settings/ }));
  const input = screen.getByLabelText(
    "Session affinity header",
  ) as HTMLInputElement;
  expect(input.value).toBe("");
  await user.click(
    screen.getByRole("combobox", { name: "Gateway session affinity" }),
  );
  await user.click(await screen.findByRole("option", { name: /LiteLLM/ }));
  expect(input.value).toBe("x-litellm-session-id");
  await user.clear(input);
  await user.type(input, "x-company-session");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(state.close).toHaveBeenCalled());
  const body = state.PATCH.mock.calls[0][1].body;
  expect(body.config).toEqual({
    ...provider.config,
    session_affinity_header: "x-company-session",
  });
  expect(body.extra_headers).toEqual({});
});

it("clears a preset without changing the endpoint or static headers", async () => {
  mount();
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /Advanced settings/ }));
  const input = screen.getByLabelText(
    "Session affinity header",
  ) as HTMLInputElement;
  await user.type(input, "x-company-session");
  await user.click(
    screen.getByRole("combobox", { name: "Gateway session affinity" }),
  );
  await user.click(
    await screen.findByRole("option", { name: "Disabled (default)" }),
  );
  expect(input.value).toBe("");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(state.close).toHaveBeenCalled());
  expect(state.PATCH.mock.calls[0][1].body.config).toEqual(provider.config);
});

const customDefinition: Schema["ProviderType"] = {
  type: "acme",
  display_name: "Acme",
  supports_test: false,
  setup_url: "https://docs.example.com/model-setup",
  setup_label: "Configure Acme access",
  model_apis: ["openai.chat_completions"],
  authentication: {
    mode: "required",
    cases: [
      { field: "access", equals: "public", mode: "forbidden" },
      { field: "access", equals: "optional", mode: "optional" },
    ],
  },
  configuration_schema: {
    type: "object",
    additionalProperties: false,
    properties: {
      access: {
        title: "Access",
        type: "string",
        enum: ["public", "optional", "private"],
        default: "public",
      },
    },
  },
  credential_schema: {
    type: "object",
    required: ["authorization", "revision"],
    additionalProperties: false,
    properties: {
      authorization: {
        title: "Authorization",
        type: "object",
        required: ["token"],
        properties: {
          token: {
            title: "Token",
            type: "string",
            minLength: 1,
            writeOnly: true,
          },
        },
      },
      revision: { title: "Revision", type: "integer", minimum: 1, default: 1 },
    },
  },
};
function mountCustom(configuration = {}, definition = customDefinition) {
  state.PATCH.mockResolvedValue({ data: provider });
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { mutations: { retry: false } } })
      }
    >
      <ProviderForm
        resource={{
          value: {
            ...provider,
            type: definition.type,
            config: configuration,
            header_names: [],
          },
          etag: '"test"',
        }}
        definitions={[definition]}
        close={state.close}
        reload={async () => {}}
      />
    </QueryClientProvider>,
  );
}
it("uses custom auth defaults to hide credentials and removes saved material", async () => {
  mountCustom();
  const user = userEvent.setup();
  expect(screen.queryByLabelText("Token")).toBeNull();
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(state.close).toHaveBeenCalled());
  expect(state.PATCH.mock.calls[0][1].body).toMatchObject({
    config: { access: "public" },
    credential: null,
  });
});
it("renders a custom conditional credential with nested secrets and a numeric value", async () => {
  mountCustom();
  const user = userEvent.setup();
  await user.click(screen.getByRole("combobox", { name: "Access" }));
  await user.click(await screen.findByRole("option", { name: "private" }));
  await user.click(screen.getByRole("button", { name: "Replace" }));
  expect(screen.getByLabelText("Token").getAttribute("type")).toBe("password");
  expect(screen.getByLabelText("Revision").getAttribute("type")).toBe("number");
  await user.type(screen.getByLabelText("Token"), "nested-secret");
  await user.clear(screen.getByLabelText("Revision"));
  await user.type(screen.getByLabelText("Revision"), "7");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(state.close).toHaveBeenCalled());
  expect(state.PATCH.mock.calls[0][1].body).toMatchObject({
    config: { access: "private" },
    credential: { authorization: { token: "nested-secret" }, revision: 7 },
  });
});

it("renders definition-owned custom help and hides unsupported connection probes", async () => {
  mountCustom({ access: "private" });
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Replace" }));
  expect(
    screen
      .getByRole("link", { name: "Configure Acme access" })
      .getAttribute("href"),
  ).toBe("https://docs.example.com/model-setup");
  expect(screen.queryByRole("button", { name: "Check connection" })).toBeNull();
});
it("offers a probe only for a definition with the operation and permits absent help", async () => {
  mountCustom(
    { access: "private" },
    {
      ...customDefinition,
      supports_test: true,
      setup_url: null,
      setup_label: null,
    },
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Replace" }));
  expect(
    screen.queryByRole("link", { name: "Configure Acme access" }),
  ).toBeNull();
  expect(
    screen.getByRole("button", { name: "Check connection" }),
  ).toBeDefined();
});

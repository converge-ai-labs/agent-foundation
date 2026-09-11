import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { ProviderForm } from "./provider-form";

const state = vi.hoisted(() => ({ PATCH: vi.fn(), close: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: { PATCH: state.PATCH } }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
const provider: Schema["ModelProvider"] = {
  id: "mprov_test",
  organization_id: "org_test",
  workspace_id: "ws_test",
  type: "deepseek",
  name: "DeepSeek Gateway",
  configuration: {
    base_url: "https://gateway.example/v1",
  },
  credential_configured: true,
  header_names: ["x-gateway"],
  enabled: true,
  created_by: { principal_type: "user", principal_id: "usr_test" },
  updated_by: { principal_type: "user", principal_id: "usr_test" },
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
        scope={{ kind: "workspace", id: "ws_test" }}
        resource={{ value: provider, etag: '"test"' }}
        definitions={[
          {
            type: "deepseek",
            display_name: "DeepSeek",
            supported_model_apis: ["openai.chat_completions"],
            default_model_api: "openai.chat_completions",
            supports_model_discovery: true,
            credential_schema: { type: "string" },
            configuration_schema: {
              type: "object",
              additionalProperties: false,
              properties: {
                base_url: { type: "string" },
                extra_headers: {
                  type: "object",
                  additionalProperties: { type: "string" },
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
  const body = state.PATCH.mock.calls[0][1].body;
  expect(body.extra_headers).toEqual({ "x-gateway": "replacement" });
  expect(body.configuration).toEqual(provider.configuration);
  expect(body).not.toHaveProperty("credential");
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

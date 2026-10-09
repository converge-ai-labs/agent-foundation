import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { Page } from "../../shared/page";
import { Providers } from "./providers";
import { ProviderForm } from "./provider-form";

const state = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  PATCH: vi.fn(),
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: state, workspace: () => state }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test" }, can: () => true }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it.each(["default", "public", "confidential"])(
  "saves %s OAuth registration and keeps first-provider authorization open",
  async (registration) => {
    const confidential = registration === "confidential";
    const provider = {
      id: "mprov_test",
      workspace_id: "ws_test",
      type: "openai_chatgpt",
      name: "ChatGPT",
      config: {},
      enabled: true,
      header_names: [],
    } as unknown as Schema["Provider"];
    const definition: Schema["ProviderType"] = {
      setup_label: null,
      setup_url: null,
      supports_test: false,
      type: "openai_chatgpt",
      display_name: "ChatGPT",
      oauth_scheme: "chatgpt",
      authentication: {
        mode: "forbidden",
        cases: [
          {
            field: "token_endpoint_auth_method",
            equals: "client_secret_basic",
            mode: "required",
          },
        ],
      },
      credential_schema: {
        type: "object",
        required: ["client_secret"],
        properties: {
          client_secret: {
            type: "string",
            title: "OAuth client secret",
            minLength: 1,
          },
        },
      },
      configuration_schema: {
        type: "object",
        properties: {
          client_id: {
            anyOf: [{ type: "string", minLength: 1 }, { type: "null" }],
            title: "OAuth client ID",
            default: null,
          },
          redirect_uri: {
            anyOf: [{ type: "string" }, { type: "null" }],
            title: "Callback URL",
            default: null,
          },
          token_endpoint_auth_method: {
            type: "string",
            enum: ["none", "client_secret_basic"],
            title: "Token endpoint authentication",
            default: "none",
          },
        },
      },
    };
    let created = false;
    state.GET.mockImplementation(async (path: string) => ({
      data: path.includes("provider-types")
        ? { items: [definition] }
        : path.endsWith("/authorization")
          ? { state: "disconnected", provider_id: provider.id }
          : { items: created ? [provider] : [], next_cursor: null },
    }));
    state.POST.mockImplementation(async (path: string) => {
      if (path.endsWith("/authorize"))
        return {
          data: {
            attempt_id: "oauth_test",
            authorization_url:
              "https://auth.openai.com/api/accounts/authorize?state=synthetic",
            expires_at: "2099-01-01T00:00:00Z",
          },
        };
      created = true;
      return { data: provider };
    });
    render(
      <QueryClientProvider
        client={
          new QueryClient({
            defaultOptions: {
              queries: { retry: false },
              mutations: { retry: false },
            },
          })
        }
      >
        <MemoryRouter>
          <Page title="Providers">
            <Providers />
          </Page>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const user = userEvent.setup();
    await screen.findByText("No model providers yet");
    await user.click(screen.getByRole("button", { name: "Add provider" }));
    await user.click(
      await screen.findByRole("button", { name: /ChatGPT.*subscription/ }),
    );
    expect(screen.queryByLabelText(/OAuth client ID/i)).toBeNull();
    expect(screen.queryByLabelText(/Callback URL/i)).toBeNull();
    expect(
      screen.queryByRole("combobox", {
        name: /Token endpoint authentication/i,
      }),
    ).toBeNull();
    if (registration !== "default") {
      await user.click(
        screen.getByRole("button", { name: "Advanced settings" }),
      );
      await user.type(
        screen.getByLabelText(/OAuth client ID/i),
        "provider-client",
      );
      await user.type(
        screen.getByLabelText(/Callback URL/i),
        "https://agent.example.com/registered/callback",
      );
    }
    expect(screen.queryByLabelText(/OAuth client secret/i)).toBeNull();
    if (confidential) {
      await user.click(
        screen.getByRole("combobox", {
          name: /Token endpoint authentication/i,
        }),
      );
      await user.click(
        await screen.findByRole("option", { name: "client_secret_basic" }),
      );
      await user.type(
        screen.getByLabelText(/OAuth client secret/i),
        "synthetic-secret",
      );
    }
    await user.click(screen.getByRole("button", { name: "Add provider" }));
    await screen.findByRole("table", { hidden: true });
    await user.click(
      await screen.findByRole("button", { name: "Continue with ChatGPT" }),
    );
    await screen.findByLabelText("Complete callback URL");
    const body = state.POST.mock.calls[0][1].body;
    expect(body.type).toBe("openai_chatgpt");
    expect(body.config).toEqual({
      client_id: registration === "default" ? null : "provider-client",
      redirect_uri:
        registration === "default"
          ? null
          : "https://agent.example.com/registered/callback",
      token_endpoint_auth_method: confidential ? "client_secret_basic" : "none",
    });
    if (confidential)
      expect(body.credential).toEqual({ client_secret: "synthetic-secret" });
    else expect(body).not.toHaveProperty("credential");
    expect(screen.getByRole("dialog")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Done" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    // Editing keeps the custom registration and write-only secret intact while collapsed.
    state.PATCH.mockResolvedValue({ data: provider });
    render(
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <ProviderForm
          resource={{
            value: {
              ...provider,
              config: body.config,
              credential_configured: confidential,
            },
            etag: '"provider:1"',
          }}
          definitions={[definition]}
          close={() => {}}
          reload={async () => {}}
        />
      </QueryClientProvider>,
    );
    expect(screen.queryByLabelText(/OAuth client ID/i)).toBeNull();
    expect(screen.queryByLabelText(/OAuth client secret/i)).toBeNull();
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(state.PATCH).toHaveBeenCalledOnce());
    expect(state.PATCH.mock.calls[0][1].body.config).toEqual(body.config);
    expect(state.PATCH.mock.calls[0][1].body).not.toHaveProperty("credential");
  },
);

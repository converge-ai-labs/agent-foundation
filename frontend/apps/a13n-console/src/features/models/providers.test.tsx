import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { Page } from "../../shared/page";
import { Providers } from "./providers";

const state = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }));
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

it("keeps first-provider authorization open when the empty collection becomes a table", async () => {
  const provider = {
    id: "mprov_test",
    workspace_id: "ws_test",
    type: "openai_chatgpt",
    name: "ChatGPT",
    config: {},
    enabled: true,
    header_names: [],
  } as unknown as Schema["Provider"];
  const definition = {
    type: "openai_chatgpt",
    display_name: "ChatGPT",
    oauth_scheme: "chatgpt",
    authentication: { mode: "forbidden" },
    credential_schema: null,
    configuration_schema: { type: "object", properties: {} },
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
  await user.click(screen.getByRole("button", { name: "Add provider" }));
  await screen.findByRole("table", { hidden: true });
  await user.click(
    await screen.findByRole("button", { name: "Sign in with ChatGPT" }),
  );
  await screen.findByLabelText("Complete callback URL");
  expect(state.POST.mock.calls[0][1].body.type).toBe("openai_chatgpt");
  expect(screen.getByRole("dialog")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Done" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { type Schema } from "../../shared/api";
import { ProviderAuthorization } from "./provider-authorization";

const state = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  DELETE: vi.fn(),
  writable: true,
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ workspace: () => state }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ can: () => state.writable }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
const provider = {
  id: "mprov_test",
  workspace_id: "ws_test",
} as Schema["Provider"];
function mount(onSave = vi.fn()) {
  state.GET.mockResolvedValue({
    data: { state: "disconnected", provider_id: provider.id },
  });
  state.POST.mockImplementation(async (path) => ({
    data: path.endsWith("/authorize")
      ? {
          attempt_id: "oauth_test",
          authorization_url:
            "https://auth.openai.com/api/accounts/authorize?state=synthetic",
          expires_at: "2099-01-01T00:00:00Z",
        }
      : { state: "connected" },
  }));
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
      <form
        onSubmit={(event) => {
          event.preventDefault();
          onSave();
        }}
      >
        <ProviderAuthorization provider={provider} />
      </form>
    </QueryClientProvider>,
  );
}
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
  state.writable = true;
});
it("starts workspace authorization, posts the full callback, clears secret input and reports unconfirmed revocation", async () => {
  mount();
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Sign in with ChatGPT" }),
  );
  const input = await screen.findByLabelText("Complete callback URL");
  expect(input.getAttribute("type")).toBe("password");
  expect(screen.getByText("Waiting for sign-in")).toBeTruthy();
  expect(screen.queryByText("Not connected")).toBeNull();
  expect(
    screen
      .getByRole("link", { name: "Continue authorization" })
      .getAttribute("target"),
  ).toBe("_blank");
  const url =
    "http://127.0.0.1:1456/auth/callback?state=synthetic&code=synthetic-code&client_id=issued";
  await user.type(input, url);
  state.GET.mockResolvedValue({
    data: {
      state: "connected",
      client_id: "issued",
      email: "test@example.test",
    },
  });
  await user.click(screen.getByRole("button", { name: "Complete sign-in" }));
  await waitFor(() =>
    expect(screen.queryByLabelText("Complete callback URL")).toBeNull(),
  );
  expect(state.POST.mock.calls[1][1].body).toEqual({
    attempt_id: "oauth_test",
    callback_url: url,
  });
  expect(sessionStorage.length).toBe(0);
  state.DELETE.mockResolvedValue({
    data: { local_tokens_cleared: true, revocation_confirmed: false },
  });
  await user.click(await screen.findByRole("button", { name: "Disconnect" }));
  await screen.findByText(
    "Local tokens were cleared. OpenAI revocation was not confirmed.",
  );
});
it("submits callback Enter without saving provider edits and can cancel pending authorization", async () => {
  const onSave = vi.fn();
  mount(onSave);
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Sign in with ChatGPT" }),
  );
  const input = await screen.findByLabelText("Complete callback URL");
  await user.click(input);
  await user.keyboard("{Enter}");
  expect(state.POST).toHaveBeenCalledTimes(1);
  expect(onSave).not.toHaveBeenCalled();
  await user.type(input, "http://127.0.0.1:1456/auth/callback?code=synthetic");
  await user.keyboard("{Enter}");
  await waitFor(() => expect(state.POST).toHaveBeenCalledTimes(2));
  expect(onSave).not.toHaveBeenCalled();
  expect(state.POST.mock.calls[1][1].body.callback_url).toContain(
    "code=synthetic",
  );
  await user.click(
    await screen.findByRole("button", { name: "Sign in with ChatGPT" }),
  );
  state.DELETE.mockResolvedValue({ data: { local_tokens_cleared: true } });
  await user.click(
    await screen.findByRole("button", { name: "Cancel authorization" }),
  );
  await waitFor(() =>
    expect(screen.queryByLabelText("Complete callback URL")).toBeNull(),
  );
  expect(screen.queryByText("Waiting for sign-in")).toBeNull();
  expect(screen.getByText("Not connected")).toBeTruthy();
});

it("leaves authorization metadata readable but refuses viewer actions", async () => {
  state.writable = false;
  mount();
  const button = await screen.findByRole("button", {
    name: "Sign in with ChatGPT",
  });
  expect((button as HTMLButtonElement).disabled).toBe(true);
  expect(state.POST).not.toHaveBeenCalled();
});

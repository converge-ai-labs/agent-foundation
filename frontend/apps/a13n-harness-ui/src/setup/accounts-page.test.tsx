// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { AccountsPage } from "./accounts";
import { createTransport } from "../transport/client";
import { TransportContext } from "../transport/context";

const clients: QueryClient[] = [];
afterEach(() => {
  cleanup();
  clients.forEach((client) => client.clear());
  vi.unstubAllGlobals();
});
const connections = [
  { provider: "codex", label: "Codex", login_methods: ["device"] },
  { provider: "grok", label: "Grok", login_methods: ["device"] },
  {
    provider: "copilot",
    label: "GitHub Copilot",
    login_methods: ["device"],
    source_selection: true,
  },
  {
    provider: "chatgpt",
    label: "ChatGPT",
    login_methods: ["browser", "manual_callback"],
    source_selection: true,
  },
];
function mount(
  connected = false,
  pending: "copilot" | "chatgpt" | false = false,
) {
  let active: Record<string, unknown> = pending
    ? {
        session_id: "login-test",
        provider: pending,
        state: "waiting",
        method: pending === "copilot" ? "device" : "browser",
        user_code: pending === "copilot" ? "1234" : null,
      }
    : { session_id: null };
  const writes: { path: string; body: any }[] = [];
  const json = (value: unknown) =>
    new Response(JSON.stringify(value), {
      headers: { "Content-Type": "application/json" },
    });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (request.method === "POST") {
        const body = await request.json();
        writes.push({ path, body });
        active = {
          session_id: "login-test",
          provider: body.provider,
          state: "waiting",
          method: body.method,
          verification_url: "https://login.example.com/verify",
          user_code: body.method === "device" ? "1234" : null,
        };
        return json(active);
      }
      if (path === "/api/models/choices")
        return json({
          connections: connections.map((account) => ({
            id: account.provider,
            account,
          })),
        });
      if (path === "/api/subscriptions")
        return json({ accounts: [], groups: [], adoption: {} });
      if (path === "/api/auth/credentials") return json([]);
      if (path === "/api/auth/logins") return json(active);
      if (path === "/api/auth/logins/login-test") return json(active);
      if (path.endsWith("/sources")) return json([]);
      if (path.startsWith("/api/auth/accounts/"))
        return json({
          availability:
            connected && path.endsWith("/chatgpt") ? "available" : "missing",
          usable: connected && path.endsWith("/chatgpt"),
          required_action: connected ? null : "login",
          account_id:
            connected && path.endsWith("/chatgpt") ? "chatgpt-work" : null,
        });
      throw new Error(`Unexpected request: ${path}`);
    }),
  );
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(queries);
  render(
    <QueryClientProvider client={queries}>
      <TransportContext value={createTransport("", () => {})}>
        <AccountsPage />
      </TransportContext>
    </QueryClientProvider>,
  );
  return {
    writes,
    completeLogin: async () => {
      connected = true;
      active = { session_id: null };
      await queries.invalidateQueries({ queryKey: ["active-login"] });
    },
  };
}

it.each(["copilot", "chatgpt"])(
  "connects %s through the common Add account flow",
  async (provider) => {
    const { writes } = mount();
    await screen.findByText(
      "No accounts connected. Add an account to get started.",
    );
    expect(
      screen.queryByRole("button", { name: "Connect account" }),
    ).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Add account" }));
    const user = userEvent.setup();
    await user.click(screen.getByRole("combobox", { name: "Provider" }));
    const label = connections.find((item) => item.provider === provider)!.label;
    await user.click(await screen.findByRole("option", { name: label }));
    expect(screen.queryByLabelText("Account name")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    await screen.findByText("Choose account source");
    if (provider === "chatgpt")
      expect(screen.getByText("Advanced login options")).toBeTruthy();
    fireEvent.click(
      await screen.findByRole("button", { name: "Connect account" }),
    );
    await waitFor(() => expect(writes).toHaveLength(1));
    expect(writes[0]).toEqual({
      path: "/api/auth/logins",
      body: {
        provider,
        method: provider === "chatgpt" ? "browser" : "device",
        allow_account_switch: false,
      },
    });
  },
);

it("resumes an in-progress login after returning to Accounts", async () => {
  const { writes } = mount(false, "copilot");
  fireEvent.click(
    await screen.findByRole("button", { name: "Continue sign-in" }),
  );
  await screen.findByText("1234");
  expect(writes).toHaveLength(0);
});

it("updates the account list when login completes after closing its dialog", async () => {
  const { completeLogin } = mount(false, "chatgpt");
  fireEvent.click(
    await screen.findByRole("button", { name: "Continue sign-in" }),
  );
  await screen.findByRole("button", { name: "Cancel login" });
  fireEvent.click(screen.getByRole("button", { name: "Done" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await completeLogin();
  await screen.findByText("chatgpt-work");
  expect(screen.queryByRole("button", { name: "Continue sign-in" })).toBeNull();
});

it("keeps a connected server account in the list and manages it in a dialog", async () => {
  mount(true);
  await screen.findByText("chatgpt-work");
  expect(
    screen.queryByRole("button", { name: "Reconnect account" }),
  ).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Manage account" }));
  await screen.findByRole("button", { name: "Reconnect account" });
  expect(screen.getByRole("button", { name: "Log out account" })).toBeTruthy();
});

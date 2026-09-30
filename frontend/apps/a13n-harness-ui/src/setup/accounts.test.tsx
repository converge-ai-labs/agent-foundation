// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ProviderAccount } from "./accounts";
import { createTransport, type Schema } from "../transport/client";
import { TransportContext } from "../transport/context";

const verificationUrl = "https://login.example.com/device";
let login: Schema<"LoginStatus">;
let writeText: ReturnType<typeof vi.fn>;
const json = (body: unknown) =>
  new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
  });
function mount(
  connection: Schema<"AccountConnection"> = {
    provider: "codex",
    label: "Codex",
    login_methods: ["device", "browser"],
  },
) {
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queries}>
      <TransportContext value={createTransport("", () => {})}>
        <ProviderAccount connection={connection} inline />
      </TransportContext>
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  login = {
    session_id: "login-test",
    provider: "codex",
    method: "device",
    state: "waiting",
    verification_url: verificationUrl,
    user_code: "ABCD-1234",
  };
  writeText = vi.fn().mockResolvedValue(undefined);
  vi.stubGlobal("navigator", { clipboard: { writeText } });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.startsWith("/api/auth/accounts/"))
        return json({ usable: false, required_action: "login" });
      if (path === "/api/auth/logins" || path === "/api/auth/logins/login-test")
        return json(login);
      throw new Error(`Unexpected request: ${path}`);
    }),
  );
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("recovers device login with a visible URL, ordered instructions and a copyable code", async () => {
  mount();
  const link = await screen.findByRole("link", {
    name: /login\.example\.com\/device/,
  });
  expect(link.getAttribute("href")).toBe(verificationUrl);
  expect(link.getAttribute("target")).toBe("_blank");
  expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  expect(
    screen.getAllByRole("listitem").map((item) => item.textContent),
  ).toEqual([
    expect.stringContaining("Open the verification page"),
    expect.stringContaining("Enter this device code"),
  ]);
  fireEvent.click(screen.getByRole("button", { name: "Copy code" }));
  await screen.findByRole("button", { name: "Copied" });
  expect(writeText).toHaveBeenCalledWith("ABCD-1234");
});

it.each(["rejected", "unavailable"])(
  "leaves the code selectable when clipboard access is %s",
  async (failure) => {
    if (failure === "unavailable") vi.stubGlobal("navigator", {});
    else writeText.mockRejectedValue(new Error("Permission denied"));
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Copy code" }));
    await screen.findByText(
      "Could not copy automatically. Select and copy the code above.",
    );
    expect(screen.getByText("ABCD-1234")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Copied" })).toBeNull();
  },
);

it("shows the browser verification link without inventing a device-code step", async () => {
  login = { ...login, method: "browser", user_code: null };
  mount();
  await screen.findByRole("link", { name: /login\.example\.com/ });
  expect(screen.getAllByRole("listitem")).toHaveLength(1);
  expect(screen.queryByRole("button", { name: "Copy code" })).toBeNull();
});

it.each(["succeeded", "failed", "cancelled"] as const)(
  "does not offer stale verification actions after login is %s",
  async (state) => {
    login = { ...login, state };
    mount();
    await screen.findByText(`Login: ${state}`);
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.queryByRole("button", { name: "Copy code" })).toBeNull();
  },
);

it.each(["javascript:alert(1)", "not a URL"])(
  "does not render an unsafe verification URL: %s",
  async (verification_url) => {
    login = { ...login, verification_url };
    mount();
    await screen.findByText("ABCD-1234");
    expect(screen.queryByRole("link")).toBeNull();
  },
);

it("selects a Copilot source explicitly and confirms shared logout even when expired", async () => {
  let status = {
    provider: "copilot",
    availability: "available",
    source: "file",
    usable: false,
    expiry: "expired",
    required_action: "reauthenticate",
    account_id: "test-user",
    source_id: "copilot_cli_file:/synthetic/config.json",
    shared_with_cli: true,
  };
  const selected = vi.fn();
  const removed = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path === "/api/auth/logins") return json(null);
      if (path.endsWith("/sources"))
        return json([
          {
            selection: { source: "native", account_id: "other-user" },
            label: "other-user · Host login",
            selected: false,
          },
        ]);
      if (path.endsWith("/selection")) {
        selected(await request.json());
        status = {
          ...status,
          account_id: "other-user",
          source_id: "native:/synthetic/copilot.json",
          shared_with_cli: false,
        };
        return json(status);
      }
      if (request.method === "DELETE") {
        removed();
        return json(true);
      }
      return json(status);
    }),
  );
  mount({
    provider: "copilot",
    label: "GitHub Copilot",
    login_methods: ["device"],
    source_selection: true,
  });
  const logout = await screen.findByRole("button", { name: "Log out account" });
  expect(selected).not.toHaveBeenCalled();
  expect(screen.queryByRole("combobox", { name: "Login method" })).toBeNull();
  fireEvent.click(logout);
  await screen.findByText(/also affects the official CLI and other Hosts/);
  expect(removed).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  fireEvent.click(screen.getByText("Choose account source"));
  fireEvent.click(
    await screen.findByRole("button", { name: "other-user · Host login" }),
  );
  await waitFor(() =>
    expect(selected).toHaveBeenCalledWith({
      source: "native",
      account_id: "other-user",
    }),
  );
  await screen.findByText("other-user · Host account");
});

it("defaults ChatGPT to automatic callback and posts a private full-URL fallback without persistence", async () => {
  login = { ...login, provider: "chatgpt", method: "browser", user_code: null };
  const completed = vi.fn();
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path.startsWith("/api/auth/accounts/"))
        return json({ usable: false, required_action: "login" });
      if (path.endsWith("/callback")) {
        completed(await request.json());
        return json(login);
      }
      if (path === "/api/auth/logins" || path === "/api/auth/logins/login-test")
        return json(login);
      throw new Error(`Unexpected request: ${path}`);
    }),
  );
  mount({
    provider: "chatgpt",
    label: "ChatGPT",
    login_methods: ["manual_callback", "browser"],
  });
  expect(
    (await screen.findByRole("combobox", { name: "Login method" })).textContent,
  ).toContain("Automatic browser callback");
  fireEvent.click(
    await screen.findByText("Callback cannot reach this server?"),
  );
  const input = screen.getByLabelText("Complete callback URL");
  expect(input.getAttribute("type")).toBe("password");
  fireEvent.change(input, {
    target: { value: "http://127.0.0.1:1456/auth/callback?code=synthetic" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Complete sign-in" }));
  await waitFor(() =>
    expect(completed).toHaveBeenCalledWith({
      callback_url: "http://127.0.0.1:1456/auth/callback?code=synthetic",
    }),
  );
  await waitFor(() => expect(input).toHaveProperty("value", ""));
});

// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
function mount() {
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queries}>
      <TransportContext value={createTransport("", () => {})}>
        <ProviderAccount provider="codex" inline />
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

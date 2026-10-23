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
import { SubscriptionAccounts } from "./subscriptions";
import { createTransport } from "../transport/client";
import { TransportContext } from "../transport/context";

const account = {
  id: "acct-work",
  name: "Work",
  provider: "codex",
  enabled: true,
};
const group = {
  schema_version: "1",
  kind: "subscription_group",
  id: "group-work",
  name: "Work group",
  provider: "codex",
  strategy: "ordered",
  allow_credits: false,
  accounts: [{ account: account.id, weight: 1 }],
};
const json = (body: unknown) =>
  new Response(JSON.stringify(body), {
    headers: { "Content-Type": "application/json" },
  });
function mount() {
  const writes: { path: string; method: string; body: any }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = decodeURIComponent(new URL(request.url).pathname);
      if (request.method !== "GET") {
        const body = await request.text();
        writes.push({
          path,
          method: request.method,
          body: body ? JSON.parse(body) : null,
        });
        return json(
          path.endsWith("/reset")
            ? { code: "reset", windows_reset: 1 }
            : path === "/api/subscriptions/accounts"
              ? account
              : {},
        );
      }
      if (path === "/api/subscriptions")
        return json({
          accounts: [
            {
              account,
              authentication: { availability: "available" },
              usage: { account_id: account.id, usage: null },
            },
          ],
          groups: [group],
          adoption: {},
        });
      if (path === "/api/configuration/sources")
        return json({
          sources: [
            {
              resource_ids: [group.id],
              resource_kind: "subscription_group",
              relative_path: "subscription_groups/custom-name.yaml",
              writable: true,
            },
          ],
        });
      if (path.endsWith("/reset-vouchers"))
        return json({
          account_id: "upstream-work",
          reset_credits: {
            available_count: 1,
            credits: [
              {
                id: "voucher-one",
                status: "available",
                reset_type: "codex_rate_limits",
                title: "Weekly reset",
              },
            ],
          },
        });
      throw new Error(`Unexpected request: ${request.method} ${path}`);
    }),
  );
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <TransportContext value={createTransport("", () => {})}>
        <SubscriptionAccounts
          connections={[
            { provider: "codex", label: "Codex", login_methods: ["device"] },
          ]}
          renderConnection={(connection) => (
            <p>Connect: {connection.provider}</p>
          )}
          renderLogin={(item) => <p>Login target: {item.id}</p>}
        />
      </TransportContext>
    </QueryClientProvider>,
  );
  return writes;
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("refreshes the selected account and opens login for the newly created native identity", async () => {
  const writes = mount();
  fireEvent.click(await screen.findByRole("button", { name: "Refresh" }));
  await waitFor(() =>
    expect(writes[0]?.path).toBe(
      "/api/subscriptions/accounts/acct-work/refresh",
    ),
  );
  fireEvent.click(screen.getByRole("button", { name: "Add account" }));
  fireEvent.change(screen.getByLabelText("Account name"), {
    target: { value: "Work" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Continue" }));
  await screen.findByText("Login target: acct-work");
  expect(writes[1]?.body).toEqual({ provider: "codex", name: "Work" });
});

it("saves a group to its owning source path with credits still opt-in", async () => {
  const writes = mount();
  fireEvent.click(await screen.findByRole("button", { name: "Edit group" }));
  fireEvent.change(screen.getByLabelText("Group name"), {
    target: { value: "Team" },
  });
  await waitFor(() =>
    expect(
      (screen.getByRole("button", { name: "Save group" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false),
  );
  fireEvent.click(screen.getByRole("button", { name: "Save group" }));
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(writes[0].path).toBe(
    "/api/configuration/sources/subscription_groups/custom-name.yaml",
  );
  expect(JSON.parse(writes[0].body.content)).toMatchObject({
    name: "Team",
    allow_credits: false,
    accounts: group.accounts,
  });
});

it("requires voucher selection and a separate confirmation before redemption", async () => {
  const writes = mount();
  fireEvent.click(
    await screen.findByRole("button", { name: "Manage account" }),
  );
  fireEvent.click(
    await screen.findByRole("menuitem", { name: "Reset voucher…" }),
  );
  fireEvent.click(await screen.findByRole("combobox", { name: "Voucher" }));
  fireEvent.click(await screen.findByRole("option", { name: /Weekly reset/ }));
  fireEvent.click(screen.getByRole("button", { name: "Review redemption" }));
  expect(writes).toHaveLength(0);
  fireEvent.click(
    screen.getByRole("button", { name: "Confirm redeem voucher" }),
  );
  await screen.findByText(/1 windows reset/);
  expect(writes).toHaveLength(1);
  expect(writes[0]).toMatchObject({
    path: "/api/subscriptions/accounts/acct-work/reset",
    body: { account_id: "upstream-work", credit_id: "voucher-one" },
  });
  expect(writes[0].body.redeem_request_id).toBeTruthy();
});

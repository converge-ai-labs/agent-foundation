// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { SubscriptionAccounts, AccountGroups } from "./subscriptions";
import { createTransport, type Schema } from "../transport/client";
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
const voucherCredits: Schema<"ResetCredit">[] = [
  { id: "undated", status: "available", reset_type: "codex_rate_limits" },
  {
    id: "later",
    status: "available",
    reset_type: "codex_rate_limits",
    title: "Later reset",
    expires_at: "2099-02-02T00:00:00Z",
  },
  {
    id: "voucher-one",
    status: "available",
    reset_type: "codex_rate_limits",
    title: "Weekly reset",
    description:
      "Thanks for using Codex! You've been granted one free rate limit reset.",
    expires_at: "2099-02-01T00:00:00Z",
  },
];
function mount({
  credits = voucherCredits,
  failResetOnce = false,
  groups = false,
} = {}) {
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
        if (path.endsWith("/reset") && failResetOnce) {
          failResetOnce = false;
          return new Response(JSON.stringify({ detail: "Outcome unknown" }), {
            status: 503,
            headers: { "Content-Type": "application/json" },
          });
        }
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
            available_count: credits.length,
            credits,
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
        {groups ? (
          <MemoryRouter initialEntries={["/?tab=groups"]}>
            <AccountGroups />
          </MemoryRouter>
        ) : (
          <SubscriptionAccounts
            connections={[
              { provider: "codex", label: "Codex", login_methods: ["device"] },
            ]}
            renderConnection={(connection) => (
              <p>Connect: {connection.provider}</p>
            )}
            renderLogin={(item) => <p>Login target: {item.id}</p>}
          />
        )}
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
  const writes = mount({ groups: true });
  fireEvent.click(await screen.findByRole("button", { name: "Manage group" }));
  expect(screen.queryByRole("dialog")).toBeNull();
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

it("lists vouchers by expiry and requires confirmation after choosing Use", async () => {
  const writes = mount();
  fireEvent.click(
    await screen.findByRole("button", { name: "Manage account" }),
  );
  fireEvent.click(
    await screen.findByRole("menuitem", { name: "Reset voucher…" }),
  );
  const list = await screen.findByRole("list", {
    name: "Available reset vouchers",
  });
  const rows = within(list).getAllByRole("listitem");
  expect(rows.map((row) => row.querySelector("strong")?.textContent)).toEqual([
    "Weekly reset",
    "Later reset",
    "Reset voucher",
  ]);
  expect(
    within(rows[0]).getByText(voucherCredits[2].description!),
  ).toBeTruthy();
  fireEvent.click(within(rows[0]).getByRole("button", { name: "Use" }));
  expect(screen.getByText(/Account: Work · upstream-work/)).toBeTruthy();
  expect(writes).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(writes).toHaveLength(0);
  fireEvent.click(screen.getAllByRole("button", { name: "Use" })[0]);
  fireEvent.click(screen.getByRole("button", { name: "Confirm use voucher" }));
  await screen.findByText(/1 windows reset/);
  expect(writes).toHaveLength(1);
  expect(writes[0]).toMatchObject({
    path: "/api/subscriptions/accounts/acct-work/reset",
    body: { account_id: "upstream-work", credit_id: "voucher-one" },
  });
  expect(writes[0].body.redeem_request_id).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Use" })).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Confirm use voucher" }),
  ).toBeNull();
});

it("retries an uncertain redemption with the same account, voucher, and request ID", async () => {
  const writes = mount({ failResetOnce: true });
  fireEvent.click(
    await screen.findByRole("button", { name: "Manage account" }),
  );
  fireEvent.click(
    await screen.findByRole("menuitem", { name: "Reset voucher…" }),
  );
  fireEvent.click((await screen.findAllByRole("button", { name: "Use" }))[0]);
  fireEvent.click(screen.getByRole("button", { name: "Confirm use voucher" }));
  const retry = await screen.findByRole("button", {
    name: "Retry use voucher",
  });
  expect(
    (screen.getByRole("button", { name: "Cancel" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  fireEvent.click(retry);
  await screen.findByText(/1 windows reset/);
  expect(writes).toHaveLength(2);
  expect(writes[1]).toEqual(writes[0]);
});

it("shows an empty state without a redemption action when no vouchers are available", async () => {
  const writes = mount({ credits: [] });
  fireEvent.click(
    await screen.findByRole("button", { name: "Manage account" }),
  );
  fireEvent.click(
    await screen.findByRole("menuitem", { name: "Reset voucher…" }),
  );
  await screen.findByText("No available reset vouchers.");
  expect(screen.queryByRole("button", { name: "Use" })).toBeNull();
  expect(writes).toHaveLength(0);
});

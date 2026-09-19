// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { TransportContext } from "../transport/context";
import { createTransport } from "../transport/client";
import { DevicesSection } from "./devices";
import { DeviceFields } from "./device-fields";

let queries: QueryClient;
let requests: string[];
let pending: boolean;
let registration: "paired" | "revoked" | null;
const device = {
  id: "device-one",
  name: "My workstation",
  transport: "websocket",
};
function json(value: unknown) {
  return new Response(JSON.stringify(value), {
    headers: { "Content-Type": "application/json" },
  });
}
beforeEach(() => {
  queries = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  requests = [];
  pending = true;
  registration = null;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      requests.push(`${request.method} ${path}`);
      if (path === "/api/configuration/sources") return json({ sources: [] });
      if (path === "/api/device-pairings")
        return json(
          pending
            ? [
                {
                  pairing_id: "pair-one",
                  device_id: "native-one",
                  name: device.name,
                  verification_code: "ABCD-1234",
                  expires_at: "2099-01-01T12:00:00Z",
                },
              ]
            : [],
        );
      if (path === "/api/device-pairings/pair-one/approve") {
        pending = false;
        registration = "paired";
        return json({ ...device, registration });
      }
      if (path === "/api/device-pairings/pair-one/reject") {
        pending = false;
        return new Response(null, { status: 204 });
      }
      if (path === "/api/devices")
        return json(registration ? [{ ...device, registration }] : []);
      if (path === "/api/devices/device-one/revoke") {
        registration = "revoked";
        return json({ ...device, registration });
      }
      if (path === "/api/devices/device-one")
        return json({ ...device, registration, available: true });
      throw new Error(`Unexpected request ${path}`);
    }),
  );
});
afterEach(() => {
  cleanup();
  queries.clear();
  vi.unstubAllGlobals();
});
function mount() {
  render(
    <MemoryRouter>
      <QueryClientProvider client={queries}>
        <TransportContext value={createTransport("fixture", () => {})}>
          <DevicesSection />
        </TransportContext>
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

it("approves the matching Device and independently revokes its online registration", async () => {
  const user = userEvent.setup();
  mount();
  expect(await screen.findByText("ABCD-1234")).not.toBeNull();
  await user.click(screen.getByRole("button", { name: "Approve Device" }));
  await waitFor(() =>
    expect(screen.queryByText("Waiting for approval")).toBeNull(),
  );
  await user.click(
    await screen.findByRole("button", { name: "Check connection" }),
  );
  expect(await screen.findByText("Online")).not.toBeNull();
  await user.click(screen.getByRole("button", { name: "Revoke" }));
  const dialog = await screen.findByRole("dialog");
  expect(
    within(dialog).getByText(/Runs using it lose their connection/),
  ).not.toBeNull();
  await user.click(
    within(dialog).getByRole("button", { name: "Revoke connection" }),
  );
  expect(
    await screen.findByText("Revoked · connections are blocked"),
  ).not.toBeNull();
  expect(requests).toContain("POST /api/devices/device-one/revoke");
  expect(screen.queryByRole("button", { name: "Check connection" })).toBeNull();
});

it("rejects an unrecognized request without adding a Device", async () => {
  const user = userEvent.setup();
  mount();
  await user.click(await screen.findByRole("button", { name: "Reject" }));
  await waitFor(() => expect(screen.queryByText("ABCD-1234")).toBeNull());
  expect(screen.getByText("No Devices connected yet.")).not.toBeNull();
  expect(requests).not.toContain("POST /api/device-pairings/pair-one/approve");
});

it("offers one connect command before manual credential configuration", async () => {
  const user = userEvent.setup();
  mount();
  await user.click(screen.getByRole("button", { name: "Connect Device" }));
  const dialog = await screen.findByRole("dialog");
  expect(
    within(dialog).getByText(`a13n-envd connect ${window.location.origin}`),
  ).not.toBeNull();
  expect(
    within(dialog).getByText(/localhost only works on this computer/),
  ).not.toBeNull();
  expect(within(dialog).getByText(/no new approval is needed/)).not.toBeNull();
});

it("keeps paired credential management out of the manual API-key editor", () => {
  render(
    <DeviceFields
      source={JSON.stringify({
        device_id: "native-one",
        transport: { kind: "websocket" },
        authentication: {
          kind: "paired",
          credential_digest: "a".repeat(64),
          revoked: false,
        },
      })}
      onChange={vi.fn()}
    />,
  );
  expect(screen.getByText("Paired Device")).not.toBeNull();
  expect(screen.queryByText("Credential source")).toBeNull();
  expect(screen.queryByText("a".repeat(64))).toBeNull();
});

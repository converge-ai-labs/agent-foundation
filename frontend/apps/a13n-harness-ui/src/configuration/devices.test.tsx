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
import { NewProject } from "../conversations/new-project";
import { parse } from "yaml";
import { DeviceFields } from "./device-fields";

let queries: QueryClient;
let requests: string[];
let pending: boolean;
let savedContent: string | undefined;
let online: boolean;
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
  savedContent = undefined;
  online = true;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      requests.push(`${request.method} ${path}`);
      if (path === "/api/status")
        return json({ public_origin: "https://harness.example" });
      if (path === "/api/selectors")
        return json({ agents: [], models: [], environments: [] });
      if (path === "/api/setup") return json({});
      if (
        decodeURIComponent(path).startsWith(
          "/api/configuration/sources/projects/",
        ) &&
        request.method === "PUT"
      ) {
        savedContent = (await request.json()).content;
        return json({});
      }
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
        return json({
          ...device,
          registration,
          available: online,
          default_working_directory: "/work",
        });
      throw new Error(`Unexpected request ${path}`);
    }),
  );
});
afterEach(() => {
  cleanup();
  queries.clear();
  vi.unstubAllGlobals();
});
function mount(component = <DevicesSection />) {
  render(
    <MemoryRouter>
      <QueryClientProvider client={queries}>
        <TransportContext value={createTransport("fixture", () => {})}>
          {component}
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
    within(dialog).getByText(
      "A13N_ENVD_FULL_CONTROL=0 a13n-envd connect 'https://harness.example' --computer-use false",
    ),
  ).not.toBeNull();
  expect(
    within(dialog).getByText(/localhost refers to the Device itself/),
  ).not.toBeNull();
  expect(
    within(dialog).getByText(/Saved identity and credentials are reused/),
  ).not.toBeNull();
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

it("connects inline, waits for the approved resource ID, and saves a remote-only Project only at the outer action", async () => {
  const user = userEvent.setup();
  const created = vi.fn();
  online = false;
  mount(<NewProject close={() => {}} created={created} />);
  await user.type(
    screen.getByRole("textbox", { name: "Project name" }),
    "Remote work",
  );
  await user.click(screen.getByRole("button", { name: "Add environment" }));
  await user.click(screen.getByRole("button", { name: "Connect new Device" }));
  let wizard = await screen.findByRole("dialog", { name: "Connect a Device" });
  expect(await within(wizard).findByText("ABCD-1234")).not.toBeNull();
  await user.click(
    within(wizard).getByRole("checkbox", { name: /Enable shell execution/ }),
  );
  expect(
    within(wizard).getByText(
      "A13N_ENVD_FULL_CONTROL=1 a13n-envd connect 'https://harness.example' --computer-use false",
    ),
  ).not.toBeNull();
  await user.click(
    within(wizard).getByRole("button", { name: "Approve Device" }),
  );
  expect(
    await within(wizard).findByText("Approved · waiting for Device connection"),
  ).not.toBeNull();
  expect(
    (
      within(wizard).getByRole("button", {
        name: "Choose a directory",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(requests).toContain("GET /api/devices/device-one");
  expect(requests).not.toContain("GET /api/devices/native-one");
  expect(savedContent).toBeUndefined();
  online = true;
  await queries.invalidateQueries({ queryKey: ["device", "device-one"] });
  await within(wizard).findByText("Device online");
  await user.click(
    within(wizard).getByRole("button", { name: "Choose a directory" }),
  );
  await waitFor(() =>
    expect(
      screen.queryByRole("dialog", { name: "Connect a Device" }),
    ).toBeNull(),
  );
  await user.click(screen.getByRole("button", { name: "Use Device default" }));
  await user.click(screen.getByRole("button", { name: "Use environment" }));
  await waitFor(() =>
    expect(
      screen.queryByRole("dialog", { name: "Add environment" }),
    ).toBeNull(),
  );
  expect(savedContent).toBeUndefined();
  await user.click(
    screen.getByRole("combobox", { name: "Default working location" }),
  );
  await user.click(
    screen.getByRole("option", { name: "my-workstation · /work" }),
  );
  await user.click(screen.getByRole("button", { name: "Add project" }));
  await waitFor(() => expect(created).toHaveBeenCalled());
  expect(parse(savedContent!)).toMatchObject({
    name: "Remote work",
    roots: [],
    defaults: {
      environment_bindings: [
        {
          device_id: "device-one",
          alias: "my-workstation",
          working_directory: "/work",
        },
      ],
      default_environment: "my-workstation",
    },
  });
});

it("closing a pending wizard does not reject, and closing an approved wizard does not revoke", async () => {
  const user = userEvent.setup();
  mount();
  await user.click(screen.getByRole("button", { name: "Connect Device" }));
  let wizard = await screen.findByRole("dialog", { name: "Connect a Device" });
  await user.click(within(wizard).getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(requests).not.toContain("POST /api/device-pairings/pair-one/reject");
  await user.click(screen.getByRole("button", { name: "Connect Device" }));
  wizard = await screen.findByRole("dialog", { name: "Connect a Device" });
  await user.click(
    await within(wizard).findByRole("button", { name: "Approve Device" }),
  );
  await within(wizard).findByText("Device online");
  await user.click(within(wizard).getByRole("button", { name: "Close" }));
  expect(registration).toBe("paired");
  expect(requests).not.toContain("POST /api/devices/device-one/revoke");
});

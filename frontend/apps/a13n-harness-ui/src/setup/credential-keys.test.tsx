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
import { CredentialKeys } from "./credential-keys";
import { TransportContext } from "../transport/context";
import { createTransport } from "../transport/client";

const clients: QueryClient[] = [];
afterEach(() => {
  cleanup();
  clients.forEach((client) => client.clear());
  vi.unstubAllGlobals();
});
function mount({ failSources = false, unavailableDevice = false } = {}) {
  const writes: { method: string; path: string; body: unknown }[] = [];
  const keys = ["key-work", "key-shared", "key-spare", "key-device"];
  const models = [
    {
      name: "Work Claude",
      route: "anthropic:claude",
      authentication: { kind: "api_key", credential_ref: "key-work" },
    },
    {
      name: "Shared Claude",
      route: "anthropic:claude",
      authentication: { kind: "api_key", credential_ref: "key-shared" },
    },
    {
      name: "Shared GPT",
      route: "openai-responses:gpt",
      authentication: { kind: "api_key", credential_ref: "key-shared" },
    },
  ];
  const devices = [
    {
      name: "Office Device",
      authentication: { kind: "api_key", credential_ref: "key-device" },
    },
    {
      name: "Shared Device",
      authentication: { kind: "api_key", credential_ref: "key-work" },
    },
  ];
  const json = (value: unknown) =>
    new Response(JSON.stringify(value), {
      headers: { "Content-Type": "application/json" },
    });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = decodeURIComponent(new URL(request.url).pathname);
      if (request.method !== "GET") {
        const raw = await request.text();
        const body = raw ? JSON.parse(raw) : null;
        writes.push({ method: request.method, path, body });
        if (request.method === "PUT" && !keys.includes(body.credential_ref))
          keys.push(body.credential_ref);
        if (request.method === "DELETE")
          keys.splice(keys.indexOf(path.split("/").at(-1)!), 1);
        return json({});
      }
      if (path === "/api/auth/keys")
        return json(keys.map((credential_ref) => ({ credential_ref })));
      if (path === "/api/configuration/sources") {
        if (failSources) return new Response("Unavailable", { status: 503 });
        return json({
          sources: [
            ...models.map((_, index) => ({
              relative_path: `models/${index}.yaml`,
              resource_kind: "model",
              content_available: true,
            })),
            ...devices.map((_, index) => ({
              relative_path: `devices/${index}.yaml`,
              resource_kind: "device",
              content_available: !unavailableDevice,
            })),
          ],
        });
      }
      const deviceIndex = path.match(/devices\/(\d+)/)?.[1];
      if (deviceIndex !== undefined)
        return json({
          relative_path: `devices/${deviceIndex}.yaml`,
          content: JSON.stringify(devices[Number(deviceIndex)]),
        });
      const index = Number(path.match(/models\/(\d+)/)?.[1]);
      return json({
        relative_path: `models/${index}.yaml`,
        content: JSON.stringify(models[index]),
      });
    }),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  clients.push(client);
  render(
    <MemoryRouter>
      <QueryClientProvider client={client}>
        <TransportContext value={createTransport("", () => {})}>
          <CredentialKeys connections={[]} />
        </TransportContext>
      </QueryClientProvider>
    </MemoryRouter>,
  );
  return writes;
}
it("groups keys by linked model providers, distinguishes shared and unlinked keys, and filters by model", async () => {
  mount();
  const provider = await screen.findByRole("region", {
    name: "anthropic keys",
  });
  expect(within(provider).getByText("key-work")).toBeTruthy();
  expect(
    within(provider)
      .getByRole("link", { name: "Model: Work Claude" })
      .getAttribute("href"),
  ).toContain("models%2F0.yaml");
  expect(
    within(
      screen.getByRole("region", { name: "Shared across providers keys" }),
    ).getByText("key-shared"),
  ).toBeTruthy();
  expect(
    within(
      screen.getByRole("region", {
        name: "Not linked to a Model or Device keys",
      }),
    ).getByText("key-spare"),
  ).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Filter API keys"), {
    target: { value: "Work Claude" },
  });
  expect(screen.queryByText("key-shared")).toBeNull();
});
it("adds, explicitly replaces, and confirms removal without displaying saved secrets", async () => {
  const writes = mount();
  await screen.findByText("key-work");
  fireEvent.click(screen.getByRole("button", { name: "Add API key" }));
  fireEvent.change(screen.getByLabelText("Saved key name"), {
    target: { value: "key-work" },
  });
  fireEvent.change(screen.getByLabelText("API key"), {
    target: { value: "fictional-secret" },
  });
  expect(
    (screen.getByRole("button", { name: "Save key" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  fireEvent.change(screen.getByLabelText("Saved key name"), {
    target: { value: "key-new" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save key" }));
  await screen.findByText("Saved key-new.");
  expect(writes[0].body).toEqual({
    credential_ref: "key-new",
    key: "fictional-secret",
  });
  expect(screen.queryByDisplayValue("fictional-secret")).toBeNull();
  const row = screen.getByText("key-work").closest("article")!;
  fireEvent.click(within(row).getByRole("button", { name: "Replace key" }));
  expect(screen.queryByLabelText("Saved key name")).toBeNull();
  expect((screen.getByLabelText("API key") as HTMLInputElement).value).toBe("");
  fireEvent.change(screen.getByLabelText("API key"), {
    target: { value: "fictional-replacement" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save key" }));
  await screen.findByText("Saved key-work.");
  expect(writes[1].body).toEqual({
    credential_ref: "key-work",
    key: "fictional-replacement",
  });
  fireEvent.click(within(row).getByRole("button", { name: "Remove" }));
  expect(writes).toHaveLength(2);
  fireEvent.click(screen.getByRole("button", { name: "Remove key" }));
  await waitFor(() => expect(writes).toHaveLength(3));
  expect(writes[2]).toMatchObject({
    method: "DELETE",
    path: "/api/auth/keys/key-work",
  });
});
it("keeps keys manageable when provider metadata cannot be read", async () => {
  mount({ failSources: true });
  const unknown = await screen.findByRole("region", {
    name: "Usage not resolved keys",
  });
  expect(within(unknown).getByText("key-work")).toBeTruthy();
  expect(screen.queryByText("Not linked to a Model or Device")).toBeNull();
});

it("shows Device-only and shared consumers before confirming deletion", async () => {
  const writes = mount();
  const devices = await screen.findByRole("region", { name: "Devices keys" });
  expect(within(devices).getByText("key-device")).toBeTruthy();
  expect(
    within(devices)
      .getByRole("link", { name: "Device: Office Device" })
      .getAttribute("href"),
  ).toContain("devices%2F0.yaml");
  const row = screen.getByText("key-work").closest("article")!;
  expect(
    within(row).getByRole("link", { name: "Device: Shared Device" }),
  ).toBeTruthy();
  fireEvent.click(within(row).getByRole("button", { name: "Remove" }));
  const dialog = screen.getByRole("dialog");
  expect(within(dialog).getByText("Model: Work Claude")).toBeTruthy();
  expect(within(dialog).getByText("Device: Shared Device")).toBeTruthy();
  expect(
    within(dialog).getByText(/Models and Devices referencing/),
  ).toBeTruthy();
  fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(writes).toHaveLength(0);
  fireEvent.change(screen.getByLabelText("Filter API keys"), {
    target: { value: "Office Device" },
  });
  expect(screen.getByText("key-device")).toBeTruthy();
  expect(screen.queryByText("key-work")).toBeNull();
});

it("does not call a key unlinked when Device sources cannot be read", async () => {
  mount({ unavailableDevice: true });
  await screen.findByRole("link", { name: "Model: Work Claude" });
  const unknown = screen.getByRole("region", {
    name: "Usage not resolved keys",
  });
  expect(within(unknown).getByText("key-device")).toBeTruthy();
  expect(screen.queryByText("Not linked to a Model or Device")).toBeNull();
});

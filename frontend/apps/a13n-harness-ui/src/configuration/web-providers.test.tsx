// @vitest-environment jsdom
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { parse } from "yaml";
import { createTransport, type Schema } from "../transport/client";
import { TransportContext } from "../transport/context";
import { ResourceFields } from "./fields";
import { WebOptions } from "./web-options";
import { WebProvidersSection } from "./web-provider-list";
import { SourcePage } from "./sources";
import { DraftContext, type SourceDraft } from "./drafts";
import { credentialMode } from "./web-providers";

const provider: Schema<"WebProviderType"> = {
  type: "future_cloud",
  display_name: "Future cloud",
  operations: ["search", "scrape"],
  supports_restricted_scrape: false,
  configuration_schema: {
    type: "object",
    required: ["region"],
    properties: {
      region: { type: "string", title: "Region", default: "west" },
      retries: { type: "integer", title: "Retries", default: 3 },
      nested: { $ref: "#/$defs/Nested" },
      domains: { type: "array", title: "Domains", items: { type: "string" } },
      format: {
        anyOf: [
          { type: "string", enum: ["markdown", "text"] },
          { type: "null" },
        ],
        title: "Format",
        default: null,
      },
    },
    $defs: {
      Nested: {
        type: "object",
        title: "Nested",
        properties: { label: { type: "string", title: "Nested label" } },
      },
    },
  },
  credential_schema: {
    type: "object",
    required: ["access_key", "secret_key"],
    properties: {
      access_key: { type: "string", title: "Access key", writeOnly: true },
      secret_key: { type: "string", title: "Secret key", writeOnly: true },
    },
  },
  authentication: { mode: "required" },
  setup_url: "https://provider.test/setup",
  setup_label: null,
};
const clients: QueryClient[] = [];
afterEach(() => {
  cleanup();
  clients.forEach((client) => client.clear());
  clients.length = 0;
  vi.unstubAllGlobals();
});

function fixture({
  initial,
  options = false,
  flow = false,
  failCredential = false,
}: {
  initial?: string;
  options?: boolean;
  flow?: boolean;
  failCredential?: boolean;
} = {}) {
  const writes: {
    method: string;
    path: string;
    body: Record<string, unknown>;
  }[] = [];
  const sources = new Map<string, string>();
  const drafts = new Map<string, SourceDraft>();
  const keys = ["key-existing"];
  const accounts = [
    {
      schema_version: "1",
      kind: "web_provider",
      id: "web-provider-work",
      name: "Cloud work",
      type: provider.type,
      configuration: {},
      credential: {
        access_key: { credential_ref: "key-existing" },
        secret_key: { env: "CLOUD_SECRET" },
      },
    },
    {
      schema_version: "1",
      kind: "web_provider",
      id: "web-provider-search-only",
      name: "Search only",
      type: "search_only",
      configuration: {},
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
        const body = JSON.parse(await request.text());
        writes.push({ method: request.method, path, body });
        if (path === "/api/auth/keys") {
          if (failCredential)
            return json({
              error: {
                message: "Credential save failed",
                code: "test_failure",
              },
            });
          keys.push(body.credential_ref);
          return json({ credential_ref: body.credential_ref });
        }
        if (path === "/api/configuration/validate")
          return json({ candidate_digest: "candidate" });
        const relative = path.replace("/api/configuration/sources/", "");
        sources.set(relative, body.content);
        const value = parse(body.content);
        accounts.push(value);
        return json({
          action: "created",
          relative_path: relative,
          source_digest: "saved",
          generation_digest: "new-generation",
        });
      }
      if (path === "/api/web-providers")
        return json({
          types: [
            provider,
            {
              ...provider,
              type: "search_only",
              display_name: "Search only",
              operations: ["search"],
            },
          ],
          accounts,
        });
      if (path === "/api/auth/keys")
        return json(keys.map((credential_ref) => ({ credential_ref })));
      if (path === "/api/configuration/sources")
        return json({
          generation_digest: "generation",
          sources: [...sources].map(([relative_path, content]) => ({
            relative_path,
            source_digest: "saved",
            resource_kind: "web_provider",
            resource_ids: [parse(content).id],
            writable: true,
            content_available: true,
          })),
        });
      if (path.startsWith("/api/configuration/sources/")) {
        const relative_path = path.replace("/api/configuration/sources/", "");
        return json({
          relative_path,
          content: sources.get(relative_path),
          source_digest: "saved",
          generation_digest: "generation",
          resource_kind: "web_provider",
          writable: true,
          content_available: true,
          resource_ids: [parse(sources.get(relative_path)!).id],
        });
      }
      if (path === "/api/selectors")
        return json({
          models: [],
          agents: [],
          environments: [],
          harness_plugins: [],
          mcp_servers: [],
          environment_run_extensions: [],
        });
      if (path === "/api/models/choices") return json({ connections: [] });
      if (path === "/api/catalog") return json([]);
      throw new Error(`Unexpected request: ${request.method} ${path}`);
    }),
  );
  function Fields() {
    const [source, setSource] = useState(initial ?? "");
    return (
      <>
        {options ? (
          <WebOptions source={source} index={0} onChange={setSource} />
        ) : (
          <ResourceFields source={source} onChange={setSource} />
        )}
        <output data-testid="source">{source}</output>
      </>
    );
  }
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  clients.push(client);
  render(
    <TransportContext value={createTransport("http://instance.test", () => {})}>
      <QueryClientProvider client={client}>
        <DraftContext value={drafts}>
          <MemoryRouter initialEntries={[flow ? "/settings/connections" : "/"]}>
            {flow ? (
              <Routes>
                <Route
                  path="/settings/connections"
                  element={<WebProvidersSection />}
                />
                <Route path="/settings/source" element={<SourcePage />} />
              </Routes>
            ) : (
              <Fields />
            )}
          </MemoryRouter>
        </DraftContext>
      </QueryClientProvider>
    </TransportContext>,
  );
  return { writes, sources, drafts };
}
const sourceValue = () => parse(screen.getByTestId("source").textContent!);

it("renders an unknown type's schema and patches only changed YAML fields", async () => {
  const initial = `schema_version: "1"\nkind: web_provider\nid: web-provider-work\nname: Cloud\ntype: future_cloud\nconfiguration:\n  region: east # keep region\n  nested:\n    label: old # keep nested\n    unknown: keep\ncustom: keep\ncredential:\n  access_key: {credential_ref: key-existing}\n  secret_key: {env: CLOUD_SECRET}\n`;
  const { writes } = fixture({ initial });
  const user = userEvent.setup();
  await screen.findByText("Future cloud");
  expect(screen.getByTestId("source").textContent).toBe(initial);
  fireEvent.change(screen.getByRole("textbox", { name: /Region/ }), {
    target: { value: "north" },
  });
  fireEvent.change(screen.getByRole("textbox", { name: "Nested label" }), {
    target: { value: "new" },
  });
  expect(sourceValue().configuration).toEqual({
    region: "north",
    nested: { label: "new", unknown: "keep" },
  });
  expect(screen.getByTestId("source").textContent).toContain("# keep region");
  expect(screen.getByTestId("source").textContent).toContain("# keep nested");
  await user.click(screen.getByRole("combobox", { name: "Retries" }));
  await user.click(await screen.findByRole("option", { name: "Custom value" }));
  expect(sourceValue().configuration.retries).toBe(3);
  await user.click(screen.getByRole("combobox", { name: "Retries" }));
  await user.click(await screen.findByRole("option", { name: "Use default" }));
  expect(sourceValue().configuration.retries).toBeUndefined();
  expect(sourceValue().custom).toBe("keep");
  expect(writes).toEqual([]);
});

it("saves a new secret separately and keeps only references in the account draft", async () => {
  const initial = `schema_version: "1"\nkind: web_provider\nid: web-provider-work\nname: Cloud\ntype: future_cloud\nconfiguration: {region: west}\n`;
  const { writes } = fixture({ initial });
  const user = userEvent.setup();
  const access = await screen.findByRole("group", { name: "Access key" });
  const secret = screen.getByRole("group", { name: "Secret key" });
  await user.click(
    within(access).getByRole("combobox", { name: "Credential source" }),
  );
  await user.click(
    await screen.findByRole("option", { name: "Server environment variable" }),
  );
  fireEvent.change(
    within(access).getByRole("textbox", { name: "Environment variable" }),
    { target: { value: "MY_ACCESS" } },
  );
  fireEvent.change(within(secret).getByLabelText("Secret key"), {
    target: { value: "synthetic-secret" },
  });
  expect(screen.getByTestId("source").textContent).not.toContain(
    "synthetic-secret",
  );
  await user.click(
    within(secret).getByRole("button", { name: "Save credential" }),
  );
  await waitFor(() =>
    expect(sourceValue().credential.secret_key.credential_ref).toMatch(/^key-/),
  );
  expect(writes).toHaveLength(1);
  expect(writes[0]).toMatchObject({
    path: "/api/auth/keys",
    body: { key: "synthetic-secret" },
  });
  expect(sourceValue().credential.access_key).toEqual({ env: "MY_ACCESS" });
  expect(screen.queryByDisplayValue("synthetic-secret")).toBeNull();
});

it("creates a typed draft from Connections and publishes through source validation/save", async () => {
  const { writes, drafts } = fixture({ flow: true });
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Add Web provider" }));
  await user.click(
    await screen.findByRole("combobox", { name: "Provider type" }),
  );
  await user.click(await screen.findByRole("option", { name: /Future cloud/ }));
  await user.click(screen.getByRole("button", { name: "Continue" }));
  const access = await screen.findByRole("group", { name: "Access key" });
  for (const group of [
    access,
    screen.getByRole("group", { name: "Secret key" }),
  ]) {
    await user.click(
      within(group).getByRole("combobox", { name: "Credential source" }),
    );
    await user.click(
      await screen.findByRole("option", { name: "key-existing" }),
    );
  }
  expect(writes).toHaveLength(0);
  const [path, draft] = [...drafts][0];
  expect(path).toMatch(/^web_providers\/web-provider-/);
  expect(parse(draft.content)).toMatchObject({
    type: "future_cloud",
    name: "Future cloud",
    configuration: {},
    credential: {
      access_key: { credential_ref: "key-existing" },
      secret_key: { credential_ref: "key-existing" },
    },
  });
  fireEvent.change(screen.getByRole("textbox", { name: /Region/ }), {
    target: { value: "west" },
  });
  await user.click(screen.getByText("Advanced configuration"));
  await user.click(screen.getByRole("button", { name: "Check settings" }));
  await screen.findByText("Configuration valid - not saved");
  expect(writes[0].path).toBe("/api/configuration/validate");
  await user.click(screen.getByRole("button", { name: "Create" }));
  await screen.findByText("Saved");
  expect(writes[1].path).toBe(`/api/configuration/sources/${path}`);
  expect(writes.map((item) => item.path)).not.toContain("/api/auth/keys");
});

it("filters backend operations, edits fallback order and clears Host preferences for native/off", async () => {
  const { writes } = fixture({
    options: true,
    initial: `kind: agent\ncapabilities:\n  - capability: web\n    configuration:\n      search: {mode: host, allowed_domains: [example.com]}\n      scrape: {mode: host}\n      custom: keep\n`,
  });
  const user = userEvent.setup();
  await user.click(screen.getByText("Web options"));
  await user.click(
    await screen.findByRole("combobox", { name: "Scrape backend" }),
  );
  expect(screen.queryByRole("option", { name: "Search only" })).toBeNull();
  await user.click(await screen.findByRole("option", { name: "Cloud work" }));
  await user.click(screen.getByRole("combobox", { name: "Search backend" }));
  await user.click(
    await screen.findByRole("option", { name: "Ordered fallback" }),
  );
  await user.click(
    screen.getByRole("combobox", { name: "Add fallback backend" }),
  );
  await user.click(await screen.findByRole("option", { name: "Cloud work" }));
  await user.click(
    screen.getAllByRole("button", { name: "Move backend up" })[1],
  );
  expect(
    sourceValue().capabilities[0].configuration.search.backend_priority,
  ).toEqual(["web-provider-work", "default"]);
  await user.click(screen.getByRole("combobox", { name: "Web search" }));
  await user.click(
    await screen.findByRole("option", { name: "Model-native search" }),
  );
  expect(sourceValue().capabilities[0].configuration.search).toEqual({
    mode: "native",
    allowed_domains: ["example.com"],
  });
  await user.click(screen.getByRole("combobox", { name: "Web scraping" }));
  await user.click(await screen.findByRole("option", { name: "Off" }));
  expect(sourceValue().capabilities[0].configuration.scrape).toEqual({
    mode: "off",
  });
  expect(sourceValue().capabilities[0].configuration.custom).toBe("keep");
  expect(writes).toEqual([]);
});

it("uses configuration defaults for conditional credential presence without writing them", () => {
  expect(
    credentialMode(
      {
        ...provider,
        configuration_schema: {
          properties: { anonymous: { type: "boolean", default: true } },
        },
        authentication: {
          mode: "required",
          cases: [{ field: "anonymous", equals: true, mode: "forbidden" }],
        },
      },
      {},
    ),
  ).toBe("forbidden");
});

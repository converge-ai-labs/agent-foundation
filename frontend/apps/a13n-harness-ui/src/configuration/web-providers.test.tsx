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
  supports_restricted_scrape: true,
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
const simple: Schema<"WebProviderType"> = {
  type: "simple_cloud",
  display_name: "Simple cloud",
  operations: ["search", "scrape"],
  supports_restricted_scrape: true,
  configuration_schema: { type: "object", properties: {} },
  credential_schema: {
    type: "object",
    required: ["api_key"],
    properties: {
      api_key: { type: "string", title: "API key", writeOnly: true },
    },
  },
  authentication: { mode: "required" },
  setup_url: "https://simple.test/keys",
  setup_label: "Get an API key",
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
  failValidation = false,
}: {
  initial?: string;
  options?: boolean;
  flow?: boolean;
  failCredential?: boolean;
  failValidation?: boolean;
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
    {
      schema_version: "1",
      kind: "web_provider",
      id: "web-provider-plain",
      name: "Plain scrape",
      type: "plain_scrape",
      configuration: {},
    },
  ];
  const types = [
    provider,
    {
      ...provider,
      type: "search_only",
      display_name: "Search only",
      operations: ["search"],
    },
    {
      ...provider,
      type: "plain_scrape",
      display_name: "Plain scrape",
      supports_restricted_scrape: false,
    },
    simple,
  ];
  const json = (value: unknown, status = 200) =>
    new Response(JSON.stringify(value), {
      status,
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
          return failValidation
            ? json(
                {
                  error: {
                    message: "Account configuration is invalid",
                    code: "web_provider_configuration_invalid",
                  },
                },
                422,
              )
            : json({ candidate_digest: "candidate" });
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
      if (path === "/api/web-providers") return json({ types, accounts });
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

it("adds an account with a pasted key in one step and stores only its reference", async () => {
  const { writes, sources } = fixture({ flow: true });
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Add Web provider" }),
  );
  const choice = await screen.findByRole("button", { name: /Simple cloud/ });
  expect(within(choice).getByText("Search")).toBeTruthy();
  expect(within(choice).getByText("Scrape")).toBeTruthy();
  await user.click(choice);
  expect(screen.getByRole("link", { name: "Get an API key" })).toBeTruthy();
  const add = screen.getByRole("button", { name: "Add" });
  expect((add as HTMLButtonElement).disabled).toBe(true);
  await user.type(
    screen.getByLabelText("API key", { selector: "input" }),
    "synthetic-secret",
  );
  await user.click(add);
  await waitFor(() => expect(writes).toHaveLength(3));
  expect(writes.map((item) => item.path)).toEqual([
    expect.stringMatching(/^\/api\/configuration\/validate$/),
    "/api/auth/keys",
    expect.stringMatching(/^\/api\/configuration\/sources\/web_providers\//),
  ]);
  const reference = writes[1].body.credential_ref as string;
  expect(writes[1].body.key).toBe("synthetic-secret");
  const [content] = [...sources.values()];
  expect(content).not.toContain("synthetic-secret");
  expect(parse(content)).toMatchObject({
    kind: "web_provider",
    name: "Simple cloud",
    type: "simple_cloud",
    configuration: {},
    credential: { api_key: { credential_ref: reference } },
  });
  // The validated draft already carried the same reference.
  expect(parse(writes[0].body.content as string).credential).toEqual({
    api_key: { credential_ref: reference },
  });
});

it("stores no key when the account recipe is rejected", async () => {
  const { writes } = fixture({ flow: true, failValidation: true });
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Add Web provider" }),
  );
  await user.click(await screen.findByRole("button", { name: /Simple cloud/ }));
  await user.type(
    screen.getByLabelText("API key", { selector: "input" }),
    "synthetic-secret",
  );
  await user.click(screen.getByRole("button", { name: "Add" }));
  await screen.findByText("Account configuration is invalid");
  expect(writes.map((item) => item.path)).toEqual([
    "/api/configuration/validate",
  ]);
});

it("keeps environment and saved-key sources under More options", async () => {
  const { writes, sources } = fixture({ flow: true });
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Add Web provider" }),
  );
  await user.click(await screen.findByRole("button", { name: /Future cloud/ }));
  await user.click(screen.getByText("More options"));
  await user.click(screen.getByRole("combobox", { name: "Access key" }));
  await user.click(
    await screen.findByRole("option", { name: "Server environment variable" }),
  );
  await user.type(
    screen.getByLabelText("Access key environment variable"),
    "MY_ACCESS",
  );
  await user.click(screen.getByRole("combobox", { name: "Secret key" }));
  await user.click(await screen.findByRole("option", { name: "key-existing" }));
  await user.click(screen.getByRole("button", { name: "Add" }));
  await waitFor(() => expect(sources.size).toBe(1));
  expect(writes.map((item) => item.path)).not.toContain("/api/auth/keys");
  expect(parse([...sources.values()][0])).toMatchObject({
    type: "future_cloud",
    credential: {
      access_key: { env: "MY_ACCESS" },
      secret_key: { credential_ref: "key-existing" },
    },
  });
});

const agent = (web: string) =>
  `kind: agent\ncapabilities:\n  - capability: web\n    configuration:\n${web}`;
const webValue = () => sourceValue().capabilities[0].configuration;

async function openOptions() {
  const user = userEvent.setup();
  await user.click(screen.getByText("Web options"));
  await screen.findByRole("checkbox", { name: "Use Cloud work for search" });
  return user;
}

it("lists accounts without selecting them and reuses one account for search and scrape", async () => {
  const initial = agent(
    "      search: {mode: host} # keep search\n      scrape: {mode: host}\n      custom: keep\n",
  );
  const { writes } = fixture({ options: true, initial });
  const user = await openOptions();
  for (const box of screen.getAllByRole("checkbox"))
    expect(box.getAttribute("aria-checked")).toBe("false");
  expect(screen.getByTestId("source").textContent).toBe(initial);
  expect(screen.getByText("Local DuckDuckGo search")).toBeTruthy();
  expect(
    screen.queryByRole("checkbox", { name: "Use Search only for scraping" }),
  ).toBeNull();
  await user.click(
    screen.getByRole("checkbox", { name: "Use Cloud work for search" }),
  );
  await user.click(
    screen.getByRole("checkbox", { name: "Use Cloud work for scraping" }),
  );
  expect(webValue().search).toEqual({
    mode: "host",
    backend: "web-provider-work",
  });
  expect(webValue().scrape).toEqual({
    mode: "host",
    backend: "web-provider-work",
  });
  expect(webValue().custom).toBe("keep");
  expect(screen.getByTestId("source").textContent).toContain("# keep search");
  await user.click(
    screen.getByRole("checkbox", { name: "Use Cloud work for search" }),
  );
  expect(webValue().search).toEqual({ mode: "host" });
  expect(writes).toEqual([]);
});

it("orders several chosen services as an explicit fallback", async () => {
  fixture({
    options: true,
    initial: agent("      scrape: {mode: host, backend: web-provider-work}\n"),
  });
  const user = await openOptions();
  await user.click(screen.getByText("Fallback order"));
  await user.click(
    screen.getByRole("button", { name: "Fall back to local default" }),
  );
  expect(webValue().scrape).toEqual({
    mode: "host",
    backend_priority: ["web-provider-work"],
  });
  await user.click(
    screen.getByRole("checkbox", { name: "Use Plain scrape for scraping" }),
  );
  expect(webValue().scrape.backend_priority).toEqual([
    "web-provider-work",
    "web-provider-plain",
  ]);
  expect(
    screen.getByText("Cloud work → Plain scrape → Local default"),
  ).toBeTruthy();
  await user.click(
    screen.getByRole("button", { name: "Move Local default up" }),
  );
  expect(webValue().scrape.backend_priority).toEqual([
    "web-provider-work",
    "default",
    "web-provider-plain",
  ]);
  await user.click(screen.getByRole("button", { name: "Use only Cloud work" }));
  expect(webValue().scrape).toEqual({
    mode: "host",
    backend: "web-provider-work",
  });
});

it("allows only one supporting service for domain-restricted scrape", async () => {
  fixture({
    options: true,
    initial: agent(
      "      scrape: {mode: host, allow_domains: [example.com]}\n",
    ),
  });
  const user = await openOptions();
  const plain = screen.getByRole("checkbox", {
    name: "Use Plain scrape for scraping",
  });
  expect(plain.getAttribute("aria-disabled")).toBe("true");
  expect(screen.getByRole("note").textContent).toContain(
    "local scraping and fallback are unavailable",
  );
  await user.click(
    screen.getByRole("checkbox", { name: "Use Cloud work for scraping" }),
  );
  expect(webValue().scrape).toEqual({
    mode: "host",
    allow_domains: ["example.com"],
    backend: "web-provider-work",
  });
  // The single choice can be replaced but not cleared, and has no fallback.
  expect(
    screen
      .getByRole("checkbox", { name: "Use Cloud work for scraping" })
      .getAttribute("aria-disabled"),
  ).toBe("true");
  expect(screen.queryByText("Fallback order")).toBeNull();
});

it("clears Host backend preferences when search turns native or scraping turns off", async () => {
  const { writes } = fixture({
    options: true,
    initial: agent(
      "      search: {mode: host, backend_priority: [web-provider-work, default], allow_domains: []}\n      scrape: {mode: host, backend: web-provider-work}\n      fetch: {enabled: false}\n",
    ),
  });
  const user = await openOptions();
  await user.click(screen.getByRole("combobox", { name: "Web search" }));
  await user.click(
    await screen.findByRole("option", { name: "Model-native search" }),
  );
  expect(webValue().search).toEqual({ mode: "native", allow_domains: [] });
  await user.click(screen.getByRole("switch", { name: "Web scraping" }));
  expect(webValue().scrape).toEqual({ mode: "off" });
  expect(webValue().fetch).toEqual({ enabled: false });
  expect(screen.queryByText("Services")).toBeNull();
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

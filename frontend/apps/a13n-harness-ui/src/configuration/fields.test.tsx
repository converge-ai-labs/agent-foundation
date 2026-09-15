// @vitest-environment jsdom
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { parse } from "yaml";
import { ResourceFields } from "./fields";

const get = vi.hoisted(() =>
  vi.fn(async (path: string) => ({
    data: path === "/api/auth/keys" ? [{ credential_ref: "key-work" }] : [],
  })),
);
vi.mock("../transport/context", () => ({
  useTransport: () => ({ client: { GET: get } }),
  useStatus: () => ({ data: { features: { host_files: false } } }),
  useSetup: () => ({ data: { suggested_project_path: "/srv" } }),
  useSources: () => ({ data: { sources: [] }, isPending: false }),
  useSelectors: () => ({
    data: {
      agents: [],
      environments: [],
      harness_plugins: [],
      mcp_servers: [],
      environment_run_extensions: [],
    },
    isPending: false,
  }),
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
function renderFields(initial: string) {
  function Fields() {
    const [source, setSource] = useState(initial);
    return (
      <>
        <ResourceFields source={source} onChange={setSource} />
        <output>{source}</output>
      </>
    );
  }
  render(
    <MemoryRouter>
      <QueryClientProvider
        client={
          new QueryClient({ defaultOptions: { queries: { retry: false } } })
        }
      >
        <Fields />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

it("selects saved key metadata without rewriting model settings or dropping an unresolved reference on load", async () => {
  const initial =
    'schema_version: "1"\nkind: model\nid: model-one\nname: Model\nroute: openai:custom-model\nauthentication: {kind: api_key, credential_ref: key-missing}\nsettings: {temperature: 0.3}\n';
  renderFields(initial);
  const user = userEvent.setup();
  const key = screen.getByRole("combobox", { name: "Saved key name" });
  await waitFor(() =>
    expect(key.textContent).toContain("key-missing (unavailable)"),
  );
  expect(screen.getByRole("status").textContent).toBe(initial);
  await user.click(key);
  await user.click(await screen.findByRole("option", { name: "key-work" }));
  expect(parse(screen.getByRole("status").textContent ?? "")).toEqual({
    ...parse(initial),
    authentication: { kind: "api_key", credential_ref: "key-work" },
  });
  expect(
    screen.getByRole("link", { name: "Manage API keys" }).getAttribute("href"),
  ).toBe("/settings/accounts");
  expect(get.mock.calls.map(([path]) => path)).toContain("/api/auth/keys");
});

it("keeps an empty environment-variable draft in its chosen credential mode", async () => {
  renderFields(
    'schema_version: "1"\nkind: model\nid: model-one\nname: Model\nauthentication: {kind: api_key, env: MODEL_KEY}\n',
  );
  const user = userEvent.setup();
  await user.clear(
    screen.getByRole("textbox", { name: "Environment variable" }),
  );
  expect(
    screen.getByRole("combobox", { name: "Credential source" }).textContent,
  ).toContain("Server environment variable");
  await user.type(
    screen.getByRole("textbox", { name: "Environment variable" }),
    "NEW_KEY",
  );
  expect(
    parse(screen.getByRole("status").textContent ?? "").authentication,
  ).toEqual({ kind: "api_key", env: "NEW_KEY" });
});

it("keeps HTTP transport selected while replacing its entire URL", async () => {
  renderFields(
    'schema_version: "1"\nkind: mcp_server\nid: mcp-one\nname: Remote\ntransport: {url: "https://old.example.test", headers: {custom: value}}\n',
  );
  const user = userEvent.setup();
  await user.clear(screen.getByRole("textbox", { name: "Server URL" }));
  expect(
    screen.getByRole("combobox", { name: "Transport" }).textContent,
  ).toContain("Remote HTTP");
  await user.type(
    screen.getByRole("textbox", { name: "Server URL" }),
    "https://new.example.test",
  );
  expect(parse(screen.getByRole("status").textContent ?? "").transport).toEqual(
    { url: "https://new.example.test", headers: { custom: "value" } },
  );
});

it("edits Project folders as individual rows while preserving unrelated configuration", async () => {
  const initial =
    'schema_version: "1"\nkind: project\nid: project-one\nname: Custom\nposition: 7\nroots: [{path: /one}, {path: /two}]\ndefaults: {agent: agent-main}\n';
  renderFields(initial);
  fireEvent.change(screen.getByRole("textbox", { name: "Server directory" }), {
    target: { value: "/new" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Remove directory 2" }));
  fireEvent.click(
    screen.getByRole("button", { name: "Add another directory" }),
  );
  fireEvent.change(
    screen.getByRole("textbox", { name: "Additional server directory 1" }),
    { target: { value: "/other" } },
  );
  expect(parse(screen.getByRole("status").textContent ?? "")).toEqual({
    ...parse(initial),
    roots: [{ path: "/new" }, { path: "/other" }],
  });
});

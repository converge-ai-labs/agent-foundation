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
    data:
      path === "/api/auth/keys"
        ? [{ credential_ref: "key-work" }]
        : path === "/api/models/choices"
          ? {
              connections: [
                {
                  id: "openai-responses",
                  label: "OpenAI",
                  provider: "openai-responses",
                  authentication: "api_key",
                  models: [],
                  default_model: "gpt-5.4",
                  supports_base_url: true,
                },
              ],
            }
          : path === "/api/models/catalog"
            ? { items: [], status: "unavailable" }
            : [],
  })),
);
vi.mock("../transport/context", () => ({
  useTransport: () => ({
    client: {
      GET: get,
      POST: async () => ({ data: { presets: [], native_tools: [] } }),
    },
  }),
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
        <output data-testid="source">{source}</output>
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

it("preserves explicit Sidekick opt-out and writes an empty mapping when re-enabled", async () => {
  const initial = 'schema_version: "1"\nwebui:\n  sidekick: null\n';
  renderFields(initial);
  const user = userEvent.setup();
  const sidekick = await screen.findByRole("switch", { name: "Sidekick" });
  expect(sidekick.getAttribute("aria-checked")).toBe("false");
  expect(screen.queryByRole("combobox", { name: "Sidekick agent" })).toBeNull();
  expect(screen.queryByRole("combobox", { name: "Sidekick model" })).toBeNull();
  expect(screen.getByTestId("source").textContent).toBe(initial);
  await user.click(sidekick);
  await user.click(screen.getByText("Subagent defaults"));
  expect(
    parse(screen.getByTestId("source").textContent!).webui.sidekick,
  ).toEqual({});
  expect(screen.getByRole("combobox", { name: "Sidekick agent" })).toBeTruthy();
});

it("selects saved key metadata without rewriting model settings or dropping an unresolved reference on load", async () => {
  const initial =
    'schema_version: "1"\nkind: model\nid: model-one\nname: Model\nroute: openai-responses:custom-model\nauthentication: {kind: api_key, credential_ref: key-missing}\nsettings: {temperature: 0.3}\n';
  renderFields(initial);
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Change credential" }),
  );
  const key = await screen.findByRole("combobox", {
    name: "Credential source",
  });
  await waitFor(() =>
    expect(key.textContent).toContain("key-missing (saved reference)"),
  );
  expect(screen.getByTestId("source").textContent).toBe(initial);
  await user.click(key);
  await user.click(await screen.findByRole("option", { name: "key-work" }));
  expect(parse(screen.getByTestId("source").textContent ?? "")).toEqual({
    ...parse(initial),
    authentication: { kind: "api_key", credential_ref: "key-work" },
  });
  expect(get.mock.calls.map(([path]) => path)).toContain("/api/auth/keys");
});

it("keeps an empty environment-variable draft in its chosen credential mode", async () => {
  renderFields(
    'schema_version: "1"\nkind: model\nid: model-one\nname: Model\nroute: openai-responses:custom-model\nauthentication: {kind: api_key, env: MODEL_KEY}\n',
  );
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("button", { name: "Change credential" }),
  );
  await user.clear(
    await screen.findByRole("textbox", { name: "Environment variable" }),
  );
  expect(
    screen.getByRole("combobox", { name: "Credential source" }).textContent,
  ).toContain("Server environment variable");
  await user.type(
    screen.getByRole("textbox", { name: "Environment variable" }),
    "NEW_KEY",
  );
  expect(
    parse(screen.getByTestId("source").textContent ?? "").authentication,
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
  expect(
    parse(screen.getByTestId("source").textContent ?? "").transport,
  ).toEqual({ url: "https://new.example.test", headers: { custom: "value" } });
});

it("keeps Project environment axes unspecified until explicitly edited", async () => {
  const initial =
    'schema_version: "1"\nkind: project\nid: project-one\nname: Local\nroots: [{path: /one}]\ndefaults: {agent: agent-main}\n';
  renderFields(initial);
  expect(screen.getByTestId("source").textContent).toBe(initial);
  const user = userEvent.setup();
  await user.click(screen.getByRole("combobox", { name: "Default folder" }));
  await user.click(
    await screen.findByRole("option", { name: "Local · Chat files" }),
  );
  let value = parse(screen.getByTestId("source").textContent ?? "");
  expect(value.defaults).toEqual({
    agent: "agent-main",
    default_environment: "thread-files",
  });
  await user.click(
    screen.getByRole("button", {
      name: "Local only",
    }),
  );
  value = parse(screen.getByTestId("source").textContent ?? "");
  expect(value.defaults).toEqual({
    agent: "agent-main",
    default_environment: "thread-files",
    environment_bindings: [],
  });
  await user.click(
    screen.getByRole("button", {
      name: "Keep current",
    }),
  );
  expect(parse(screen.getByTestId("source").textContent ?? "")).toEqual(
    parse(initial),
  );
});

it("allows the last local Project root to be removed only with Device bindings", async () => {
  const initial =
    'schema_version: "1"\nkind: project\nid: project-one\nname: Remote\nroots: [{path: /one}]\ndefaults:\n  agent: agent-main\n  default_environment: build\n  environment_bindings: [{device_id: device-missing, alias: build, working_directory: /remote}]\n';
  renderFields(initial);
  await screen.findByText(/Device not configured/);
  fireEvent.click(screen.getByRole("button", { name: "Remove directory 1" }));
  expect(parse(screen.getByTestId("source").textContent ?? "")).toEqual({
    ...parse(initial),
    roots: [],
  });
  expect(
    (
      screen.getByRole("button", {
        name: "Remove directory /remote",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(
    (
      screen.getByRole("button", {
        name: "Keep current",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
});

it("removes a missing Device offline and saves its replacement default with the collection", async () => {
  const initial =
    'schema_version: "1"\nkind: project\nid: project-one\nname: Mixed\nroots: [{path: /one}]\ndefaults:\n  agent: agent-main\n  default_environment: build\n  environment_bindings: [{device_id: device-missing, alias: build, working_directory: /remote}]\n';
  renderFields(initial);
  const user = userEvent.setup();
  await user.click(
    screen.getByRole("button", { name: "Remove directory /remote" }),
  );
  expect(
    (
      screen.getByRole("button", {
        name: "Remove selection",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  await user.click(
    screen.getByRole("combobox", { name: "Replacement default" }),
  );
  await user.click(await screen.findByRole("option", { name: "Local · /one" }));
  await user.click(screen.getByRole("button", { name: "Remove selection" }));
  expect(
    parse(screen.getByTestId("source").textContent ?? "").defaults,
  ).toEqual({
    agent: "agent-main",
    environment_bindings: [],
    default_environment: "workspace",
  });
  expect(
    get.mock.calls.every(([path]) => !path.startsWith("/api/devices/")),
  ).toBe(true);
});

it("authors HTTP and reverse WebSocket Devices with credential references", async () => {
  const initial =
    'schema_version: "1"\nkind: device\nid: device-one\nname: Build\ndevice_id: native-one\ntransport: {kind: http, configuration: {endpoint: https://device.example}}\nauthentication: {kind: api_key, env: DEVICE_TOKEN}\n';
  renderFields(initial);
  const user = userEvent.setup();
  await user.clear(screen.getByRole("textbox", { name: "Device endpoint" }));
  await user.type(
    screen.getByRole("textbox", { name: "Device endpoint" }),
    "https://build.example",
  );
  expect(
    parse(screen.getByTestId("source").textContent ?? "").transport
      .configuration.endpoint,
  ).toBe("https://build.example");
  await user.click(screen.getByRole("combobox", { name: "Transport" }));
  await user.click(
    await screen.findByRole("option", {
      name: "WebSocket · envd connects to this server",
    }),
  );
  expect(screen.queryByRole("textbox", { name: "Device endpoint" })).toBeNull();
  await user.click(screen.getByRole("combobox", { name: "Credential source" }));
  await user.click(
    await screen.findByRole("option", { name: "Saved API key reference" }),
  );
  await user.type(
    screen.getByRole("textbox", { name: "Saved credential reference" }),
    "key-device",
  );
  expect(parse(screen.getByTestId("source").textContent ?? "")).toEqual({
    ...parse(initial),
    transport: { kind: "websocket", configuration: {} },
    authentication: { kind: "api_key", credential_ref: "key-device" },
  });
});

it("shows default-on memory without rewriting YAML and preserves organizer choices when disabled", async () => {
  const initial =
    'schema_version: "1"\nmemory:\n  auto_organize:\n    model: model-saved\n    instructions: Keep decisions concise.\n';
  renderFields(initial);
  const user = userEvent.setup();
  expect(
    (await screen.findByRole("switch", { name: "File memory" })).getAttribute(
      "aria-checked",
    ),
  ).toBe("true");
  expect(
    screen
      .getByRole("switch", { name: "Automatic organization" })
      .getAttribute("aria-checked"),
  ).toBe("true");
  await user.click(screen.getByText("Memory updates"));
  expect(screen.getByTestId("source").textContent).toBe(initial);
  fireEvent.change(
    screen.getByRole("textbox", { name: "Update instructions" }),
    { target: { value: "Preserve decisions and preferences." } },
  );
  await user.click(
    screen.getByRole("switch", { name: "Automatic organization" }),
  );
  await user.click(screen.getByRole("switch", { name: "File memory" }));
  expect(parse(screen.getByTestId("source").textContent!).memory).toEqual({
    enabled: false,
    auto_organize: {
      enabled: false,
      model: "model-saved",
      instructions: "Preserve decisions and preferences.",
    },
  });
  expect(screen.queryByRole("combobox", { name: "Update model" })).toBeNull();
});

it("defaults organization to the global Agent model without rewriting YAML", async () => {
  const initial = 'schema_version: "1"\n';
  renderFields(initial);
  expect(
    (await screen.findByRole("combobox", { name: "Update model" })).textContent,
  ).toContain("Use default agent model");
  expect(screen.getByTestId("source").textContent).toBe(initial);
});

it("can clear an organization override to follow the global Agent model", async () => {
  renderFields(
    'schema_version: "1"\nmemory:\n  auto_organize:\n    model: model-saved\n    instructions: Keep decisions concise.\n',
  );
  const user = userEvent.setup();
  await user.click(
    await screen.findByRole("combobox", { name: "Update model" }),
  );
  await user.click(
    await screen.findByRole("option", {
      name: "Use default agent model",
    }),
  );
  expect(
    parse(screen.getByTestId("source").textContent!).memory.auto_organize,
  ).toEqual({
    instructions: "Keep decisions concise.",
  });
});

it("edits Agent working state without losing custom configuration", async () => {
  const initial =
    'schema_version: "1"\nkind: agent\nid: agent-one\nname: Helper\ncapabilities:\n  - capability: working_state\n    configuration: {task_mode: tools, notes_enabled: false}\n';
  renderFields(initial);
  const user = userEvent.setup();
  expect(
    (await screen.findByRole("switch", { name: "Tasks" })).getAttribute(
      "aria-checked",
    ),
  ).toBe("true");
  expect(
    screen.getByRole("switch", { name: "Notes" }).getAttribute("aria-checked"),
  ).toBe("false");
  expect(screen.getByTestId("source").textContent).toBe(initial);
  screen.getByRole("switch", { name: "Tasks" }).focus();
  await user.keyboard(" ");
  expect(
    parse(screen.getByTestId("source").textContent!).capabilities[0]
      .configuration,
  ).toEqual({ task_mode: "tools", notes_enabled: false, tasks_enabled: false });
});

it("allows typing multiple skill directories and preserves advanced options", async () => {
  renderFields(
    'schema_version: "1"\nkind: agent\nid: agent-one\nname: Helper\ncapabilities:\n  - capability: skills\n    configuration: {roots: [], custom: keep}\n',
  );
  const user = userEvent.setup();
  await user.click(screen.getByText("Capabilities", { exact: true }));
  const roots = await screen.findByRole("textbox", {
    name: "Additional skill directories",
  });
  await user.type(roots, "/srv/skills{Enter}/srv/team{Enter}");
  expect((roots as HTMLTextAreaElement).value).toBe("/srv/skills\n/srv/team\n");
  await user.tab();
  expect(
    parse(screen.getByTestId("source").textContent!).capabilities[0]
      .configuration,
  ).toEqual({ roots: ["/srv/skills", "/srv/team"], custom: "keep" });
});

it("edits web options without replacing nested custom constraints", async () => {
  renderFields(
    'schema_version: "1"\nkind: agent\nid: agent-one\nname: Helper\ncapabilities:\n  - capability: web\n    configuration:\n      search: {mode: auto, allowed_domains: [example.com]}\n      download: {max_bytes: 1234}\n',
  );
  const user = userEvent.setup();
  await user.click(screen.getByText("Capabilities", { exact: true }));
  await user.click(await screen.findByText("Web options"));
  await user.click(screen.getByRole("combobox", { name: "Web search" }));
  await user.click(await screen.findByRole("option", { name: "Host search" }));
  await user.click(screen.getByRole("switch", { name: "Download files" }));
  expect(
    parse(screen.getByTestId("source").textContent!).capabilities[0]
      .configuration,
  ).toEqual({
    search: { mode: "host", allowed_domains: ["example.com"] },
    download: { max_bytes: 1234, enabled: false },
  });
});

import { createClient } from "../../service-client";
import { expect, it, vi } from "vitest";
import { initialConfig } from "./configuration";
import {
  agentDependencies,
  inspectAgentDependencies,
} from "./transfer-dependencies";

function clientFor(read: (url: URL) => object) {
  const fetch = vi.fn<typeof globalThis.fetch>(
    async (input, init) =>
      new Response(
        JSON.stringify(read(new URL(new Request(input, init).url))),
        { headers: { "Content-Type": "application/json" } },
      ),
  );
  return {
    client: createClient({
      baseUrl: "http://localhost",
      auth: { type: "session" },
      fetch,
    }),
    fetch,
  };
}

it("checks every catalog page and retains an unavailable pinned version", async () => {
  const { client, fetch } = clientFor((url) => {
    if (url.pathname.endsWith("/models"))
      return {
        items: [
          { id: "mdl_local", key: "research", name: "Research", enabled: true },
        ],
      };
    if (url.pathname.endsWith("/skills"))
      return { items: [{ id: "sk_local", key: "sources", name: "Sources" }] };
    if (url.pathname.endsWith("/revisions")) return { items: [{ version: 1 }] };
    if (url.searchParams.has("cursor"))
      return {
        items: [{ id: "conn_available", name: "Local", status: "ready" }],
      };
    return {
      items: [{ id: "conn_disabled", name: "Disabled", status: "disabled" }],
      next_cursor: "next",
    };
  });
  const checks = await inspectAgentDependencies(client, "ws_target", {
    ...initialConfig("Research"),
    model: { model_key: "research" },
    skills: [{ skill_key: "sources", version: 3 }],
    connection_tools: [
      { connection_id: "conn_available" },
      { connection_id: "conn_missing" },
    ],
  });
  expect(checks.map((item) => item.available)).toEqual([
    true,
    false,
    true,
    false,
  ]);
  expect(checks[1]?.issue).toContain("Pinned version");
  expect(checks[2]?.options.map((item) => item.value)).toEqual([
    "conn_available",
  ]);
  expect(
    fetch.mock.calls.filter(([request]) =>
      new URL(new Request(request).url).pathname.endsWith("/connections"),
    ),
  ).toHaveLength(2);
  expect(
    fetch.mock.calls.every(
      ([request]) =>
        new Request(request).headers.get("X-A13N-Workspace-ID") === "ws_target",
    ),
  ).toBe(true);
});

it("only queries dependency kinds actually selected by the configuration", async () => {
  const { client, fetch } = clientFor(() => ({
    items: [
      { id: "mdl_local", key: "research", name: "Research", enabled: true },
    ],
  }));
  const checks = await inspectAgentDependencies(client, "ws_target", {
    ...initialConfig("Research"),
    model: { model_key: "research" },
  });
  expect(checks[0]?.available).toBe(true);
  expect(fetch).toHaveBeenCalledOnce();
});

it("preserves historical dedicated-environment revisions that remain visible", async () => {
  const { client } = clientFor((url) => {
    if (url.pathname.endsWith("/models"))
      return {
        items: [
          { id: "mdl_local", key: "research", name: "Research", enabled: true },
        ],
      };
    if (url.pathname.endsWith("/agents"))
      return {
        items: [
          { id: "ap_child", key: "helper", name: "Helper", enabled: true },
        ],
      };
    if (url.pathname.endsWith("/environment-templates"))
      return {
        items: [
          {
            id: "et_local",
            name: "Sandbox",
            version: 2,
            current_revision_id: "etr_new",
          },
        ],
      };
    return { id: "etr_old", template_id: "et_local", version: 1 };
  });
  const checks = await inspectAgentDependencies(client, "ws_target", {
    ...initialConfig("Research"),
    model: { model_key: "research" },
    subagents: {
      helper: {
        agent_id: "ap_child",
        environment: { mode: "dedicated", template_revision_id: "etr_old" },
      },
    },
  });
  expect(checks.every((item) => item.available)).toBe(true);
  expect(checks[2]?.options.map((item) => item.value)).toEqual([
    "etr_new",
    "etr_old",
  ]);
});

it("checks search and scrape against the shared Web Provider catalog", async () => {
  const { client, fetch } = clientFor((url) => {
    if (url.pathname.endsWith("/models"))
      return {
        items: [
          { id: "mdl_local", key: "research", name: "Research", enabled: true },
        ],
      };
    if (url.pathname.endsWith("/web-providers"))
      return {
        items: [
          {
            id: "wprov_ready",
            name: "Ready",
            type: "tavily",
            enabled: true,
            credential_configured: true,
          },
          {
            id: "wprov_disabled",
            name: "Disabled",
            type: "tavily",
            enabled: false,
            credential_configured: true,
          },
        ],
      };
    throw new Error(`Unexpected catalog: ${url.pathname}`);
  });
  const checks = await inspectAgentDependencies(client, "ws_target", {
    ...initialConfig("Research"),
    model: { model_key: "research" },
    toolsets: {
      web: {
        enabled: true,
        tools: {
          search: { config: { provider_id: "wprov_ready" } },
          scrape: { config: { provider_id: "wprov_disabled" } },
          fetch: { enabled: true },
        },
      },
    },
  });
  expect(checks.map(({ available }) => available)).toEqual([true, true, false]);
  expect(checks[2]?.options.map(({ value }) => value)).toEqual(["wprov_ready"]);
  expect(
    fetch.mock.calls.filter(([request]) =>
      new URL(new Request(request).url).pathname.endsWith("/web-providers"),
    ),
  ).toHaveLength(1);
});

it("checks and remaps memory providers without replacing behavior options", async () => {
  const { client } = clientFor((url) =>
    url.pathname.endsWith("/models")
      ? {
          items: [
            {
              id: "mdl_local",
              key: "research",
              name: "Research",
              enabled: true,
            },
          ],
        }
      : {
          items: [
            {
              id: "memprov_ready",
              name: "Team",
              type: "custom.memory",
              enabled: true,
              credential_configured: true,
            },
            {
              id: "memprov_disabled",
              name: "Old",
              type: "custom.memory",
              enabled: false,
              credential_configured: true,
            },
          ],
        },
  );
  const config = {
    ...initialConfig("Research"),
    model: { model_key: "research" },
    memory: {
      provider_id: "memprov_missing",
      scope: "agent" as const,
      auto_recall: false,
      recall_limit: 7,
    },
  };
  const checks = await inspectAgentDependencies(client, "ws_target", config);
  const memory = checks.find((item) => item.path === "memory.provider_id");
  expect(memory?.available).toBe(false);
  expect(memory?.options.map((item) => item.value)).toEqual(["memprov_ready"]);
  const replaced = agentDependencies(config)
    .find((item) => item.kind === "memory")!
    .replace("memprov_ready");
  expect(replaced.memory).toEqual({
    ...config.memory,
    provider_id: "memprov_ready",
  });
});

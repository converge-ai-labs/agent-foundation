import { createClient } from "../../service-client";
import { expect, it, vi } from "vitest";
import { initialConfig } from "./configuration";
import {
  agentDependencies,
  inspectAgentDependencies,
} from "./transfer-dependencies";

const signal = new AbortController().signal;

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
        items: [{ key: "model-local", name: "Research", enabled: true }],
      };
    if (url.pathname.endsWith("/skills"))
      return { items: [{ id: "sk_local", name: "Sources" }] };
    if (url.pathname.endsWith("/revisions"))
      return { items: [{ id: "skr_other", number: 1 }] };
    if (url.searchParams.has("cursor"))
      return {
        items: [
          {
            id: "conn_available",
            name: "Local",
            status: "ready",
            enabled: true,
          },
        ],
      };
    return {
      items: [
        {
          id: "conn_disabled",
          name: "Disabled",
          status: "ready",
          enabled: false,
        },
      ],
      next_cursor: "next",
    };
  });
  const checks = await inspectAgentDependencies(
    client,
    "ws_target",
    {
      ...initialConfig(),
      model: "model-local",
      skills: [{ skill_id: "sk_local", revision_id: "skr_pinned" }],
      connection_tools: [
        { connection_id: "conn_available" },
        { connection_id: "conn_missing" },
      ],
    },
    signal,
  );
  expect(checks.map((item) => item.available)).toEqual([
    true,
    false,
    true,
    false,
  ]);
  expect(checks[1]).toMatchObject({
    path: "skills.0.skill_id",
    options: [
      { value: "sk_local", label: "Sources · sk_local", id: "sk_local" },
    ],
  });
  expect(checks[1]?.issue).toContain("Pinned version");
  // The pinned version is looked up under the skill's ID.
  expect(
    fetch.mock.calls.some(
      ([request]) =>
        new URL(new Request(request).url).pathname ===
        "/api/v1/skills/sk_local/revisions",
    ),
  ).toBe(true);
  expect(checks[2]?.options.map((item) => item.value)).toEqual([
    "conn_available",
  ]);
  expect(
    fetch.mock.calls.filter(([request]) =>
      new URL(new Request(request).url).pathname.endsWith("/connections"),
    ),
  ).toHaveLength(2);
  const models = new URL(new Request(fetch.mock.calls[0]![0]).url);
  expect(models.pathname).toBe("/api/v1/models");
  expect(
    new Request(fetch.mock.calls[0]![0]).headers.get("X-Workspace-ID"),
  ).toBe("ws_target");
});

it("only queries dependency kinds actually selected by the configuration", async () => {
  const { client, fetch } = clientFor(() => ({
    items: [{ key: "model-local", name: "Research", enabled: true }],
  }));
  const checks = await inspectAgentDependencies(
    client,
    "ws_target",
    { ...initialConfig(), model: "model-local" },
    signal,
  );
  expect(checks[0]?.available).toBe(true);
  expect(fetch).toHaveBeenCalledOnce();
});

it("checks dedicated child environments against the same template catalog", async () => {
  const { client, fetch } = clientFor((url) => {
    if (url.pathname.endsWith("/models"))
      return {
        items: [{ key: "model-local", name: "Research", enabled: true }],
      };
    if (url.pathname.endsWith("/agents"))
      return { items: [{ id: "ap_child", name: "Helper" }] };
    return {
      items: [{ id: "et_local", name: "Sandbox", enabled: true }],
    };
  });
  const checks = await inspectAgentDependencies(
    client,
    "ws_target",
    {
      ...initialConfig(),
      model: "model-local",
      default_environment_template_id: "et_local",
      subagents: {
        helper: {
          agent_id: "ap_child",
          environment: { mode: "dedicated", template_id: "et_local" },
        },
      },
    },
    signal,
  );
  expect(checks.every((item) => item.available)).toBe(true);
  expect(
    checks
      .find((item) => item.path === "subagents.helper.environment.template_id")
      ?.options.map((item) => item.value),
  ).toEqual(["et_local"]);
  expect(
    fetch.mock.calls.filter(([request]) =>
      new URL(new Request(request).url).pathname.endsWith(
        "/environment-templates",
      ),
    ),
  ).toHaveLength(1);
});

it("blocks an unavailable root template and remaps only its identity", async () => {
  const { client } = clientFor((url) => {
    if (url.pathname.endsWith("/models"))
      return {
        items: [{ key: "model-local", name: "Research", enabled: true }],
      };
    return { items: [{ id: "et_local", name: "Sandbox", enabled: true }] };
  });
  const config = {
    ...initialConfig(),
    model: "model-local",
    default_environment_template_id: "et_source",
    retries: { tools: 2, output: 1 },
  };
  const checks = await inspectAgentDependencies(
    client,
    "ws_target",
    config,
    signal,
  );
  const root = checks.find(
    (item) => item.path === "default_environment_template_id",
  );
  expect(root?.available).toBe(false);
  expect(root?.options.map((item) => item.value)).toEqual(["et_local"]);
  const replaced = agentDependencies(config)
    .find((item) => item.path === "default_environment_template_id")!
    .replace("et_local");
  expect(replaced).toEqual({
    ...config,
    default_environment_template_id: "et_local",
  });
});

it("checks search and scrape against the shared Web Provider catalog", async () => {
  const { client, fetch } = clientFor((url) => {
    if (url.pathname.endsWith("/models"))
      return {
        items: [{ key: "model-local", name: "Research", enabled: true }],
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
  const checks = await inspectAgentDependencies(
    client,
    "ws_target",
    {
      ...initialConfig(),
      model: "model-local",
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
    },
    signal,
  );
  expect(checks.map(({ available }) => available)).toEqual([true, true, false]);
  expect(checks[2]?.options.map(({ value }) => value)).toEqual(["wprov_ready"]);
  expect(
    fetch.mock.calls.filter(([request]) =>
      new URL(new Request(request).url).pathname.endsWith("/web-providers"),
    ),
  ).toHaveLength(1);
});

it("lists every selected media model and remaps one kind at a time", () => {
  const config = {
    ...initialConfig(),
    model: "model-primary",
    media_understanding: {
      image: "model-vision",
      video: null,
      audio: "model-speech",
    },
  };
  const refs = agentDependencies(config);
  expect(
    refs.filter((ref) => ref.path.startsWith("media_understanding.")),
  ).toEqual([
    expect.objectContaining({
      path: "media_understanding.image",
      kind: "model",
      value: "model-vision",
    }),
    expect.objectContaining({
      path: "media_understanding.audio",
      kind: "model",
      value: "model-speech",
    }),
  ]);
  const image = refs.find((ref) => ref.path === "media_understanding.image")!;
  expect(image.replace("model-eyes").media_understanding).toEqual({
    image: "model-eyes",
    video: null,
    audio: "model-speech",
  });
});

it("checks the reviewer and media models against the workspace's enabled models", async () => {
  const { client, fetch } = clientFor(() => ({
    items: [
      { key: "model-primary", name: "Primary", enabled: true },
      { key: "model-vision", name: "Vision", enabled: true },
      { key: "model-judge", name: "Judge", enabled: false },
    ],
  }));
  const checks = await inspectAgentDependencies(
    client,
    "ws_target",
    {
      ...initialConfig(),
      model: "model-primary",
      media_understanding: { image: "model-vision", audio: "model-missing" },
      reviewer: { model: "model-judge" },
    },
    signal,
  );
  expect(checks.map(({ path, available }) => ({ path, available }))).toEqual([
    { path: "model", available: true },
    { path: "media_understanding.image", available: true },
    { path: "media_understanding.audio", available: false },
    { path: "reviewer.model", available: false },
  ]);
  expect(checks[3]?.options.map((option) => option.value)).toEqual([
    "model-primary",
    "model-vision",
  ]);
  // One model catalog read serves every model reference.
  expect(fetch).toHaveBeenCalledOnce();
});

it("checks default memory mounts and remaps only the memory", async () => {
  const { client, fetch } = clientFor((url) => {
    if (url.pathname.endsWith("/models"))
      return {
        items: [{ key: "model-local", name: "Research", enabled: true }],
      };
    if (url.pathname.endsWith("/memories"))
      return {
        items: [{ id: "mem_local", key: "handbook", name: "Handbook" }],
      };
    throw new Error(`Unexpected catalog: ${url.pathname}`);
  });
  const config = {
    ...initialConfig(),
    model: "model-local",
    memory_mounts: [
      { name: "handbook", memory_id: "mem_local", access: "read" as const },
      { name: "prefs", memory_id: "mem_source", access: "write" as const },
    ],
  };
  const checks = await inspectAgentDependencies(
    client,
    "ws_target",
    config,
    signal,
  );
  expect(checks.map(({ path, available }) => [path, available])).toEqual([
    ["model", true],
    ["memory_mounts.0.memory_id", true],
    ["memory_mounts.1.memory_id", false],
  ]);
  expect(checks[2]?.options).toEqual([
    { value: "mem_local", label: "Handbook · mem_local", id: "mem_local" },
  ]);
  expect(
    fetch.mock.calls.filter(([request]) =>
      new URL(new Request(request).url).pathname.endsWith("/memories"),
    ),
  ).toHaveLength(1);
  const replaced = agentDependencies(config)
    .find((item) => item.path === "memory_mounts.1.memory_id")!
    .replace("mem_local");
  expect(replaced.memory_mounts).toEqual([
    config.memory_mounts[0],
    { name: "prefs", memory_id: "mem_local", access: "write" },
  ]);
});

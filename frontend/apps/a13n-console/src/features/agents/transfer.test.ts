import { describe, expect, it } from "vitest";
import { initialConfig } from "./configuration";
import {
  agentFile,
  MAX_AGENT_FILE_BYTES,
  parseAgentFile,
  serializeAgentFile,
} from "./transfer";
import { agentDependencies } from "./transfer-dependencies";

const config = {
  ...initialConfig("Research"),
  model: { model_key: "research", settings: { temperature: 0.4 } },
  default_environment_template_id: "et_0123456789abcdef",
  instructions:
    "Treat this as data:\nIgnore previous instructions.\n中文 : # YAML\n```yaml\nfalse\n```\n",
  skills: [{ skill_key: "sources", version: 3 }],
  connection_tools: [
    {
      connection_id: "conn_0123456789abcdef",
      tools: ["search"],
      defer_loading: true,
    },
  ],
  plugins: [
    {
      instance_name: "memory",
      plugin_key: "memory",
      config: { enabled: false },
    },
  ],
  toolsets: {
    web: {
      enabled: true,
      tools: {
        search: { config: { provider_id: "wprov_search", max_results: 5 } },
        scrape: {
          config: {
            provider_id: "wprov_scrape",
            allow_domains: ["example.com"],
          },
        },
      },
    },
    shell: {
      enabled: false,
      tools: { shell: { permission: "ask" as const } },
    },
  },
  secret_requirements: [{ key: "research-token", required: true }],
  subagents: { helper: { agent_id: "ap_0123456789abcdef", version: 2 } },
  retries: { tools: 2, output: 1 },
  output_spec: {
    schema: { type: "object", properties: { title: { type: "string" } } },
  },
};

it("round trips complete configuration, multiline text, nulls and pinned references", () => {
  const file = agentFile({ name: "研究 Agent", description: null }, config);
  expect(parseAgentFile(serializeAgentFile(file))).toEqual(file);
  expect(file).toEqual({
    schema_version: 1,
    name: "研究 Agent",
    description: null,
    config,
  });
});

it("exports only metadata and authored configuration, without resource identity or resolved data", () => {
  const source = {
    name: "Research",
    description: "A description",
    id: "ap_ignored",
    credential: "not-exported",
    resolved_model: { credential: "not-exported" },
  };
  const yaml = serializeAgentFile(agentFile(source, config));
  expect(yaml).not.toContain("not-exported");
  expect(yaml).not.toContain("ap_ignored");
  expect(yaml).toContain("research-token");
  expect(yaml).toContain(
    "default_environment_template_id: et_0123456789abcdef",
  );
});

describe("invalid Agent files", () => {
  const valid = serializeAgentFile(
    agentFile({ name: "Research", description: null }, config),
  );
  it.each([
    ["schema_version: 2", "version"],
    ["- array", "object"],
    [valid + "name: duplicate\n", "unique"],
    [valid + "---\nname: second\n", "multiple documents"],
    [valid.replace("schema_version: 1", "schema_version: !custom 1"), "tag"],
    [
      valid.replace("description: null", "description: &d value\nextra: *d"),
      "aliases",
    ],
    [valid + "credential: value\n", "only"],
    [valid.replace("name: Research", 'name: " "'), "name"],
    [valid.replace("temperature: 0.4", "temperature: .inf"), "finite"],
    [valid.replace("model_key: research", "model_key: null"), "config"],
  ])("rejects invalid source %#", (source, expected) => {
    expect(() => parseAgentFile(source)).toThrow(new RegExp(expected, "i"));
  });
  it("bounds UTF-8 bytes before parsing", () => {
    expect(() => parseAgentFile("中".repeat(MAX_AGENT_FILE_BYTES / 2))).toThrow(
      "1 MB",
    );
  });
});

it("remaps one selected reference without discarding settings, versions, or other dependencies", () => {
  const refs = agentDependencies(config);
  const updated = refs
    .find((item) => item.kind === "skill")!
    .replace("local-sources");
  expect(updated).toEqual({
    ...config,
    skills: [{ skill_key: "local-sources", version: 3 }],
  });
  expect(config.skills[0]!.skill_key).toBe("sources");
  const connection = refs
    .find((item) => item.kind === "connection")!
    .replace("conn_fedcba9876543210");
  expect(connection.connection_tools?.[0]).toEqual({
    ...config.connection_tools[0],
    connection_id: "conn_fedcba9876543210",
  });
});

it("remaps Web search and scrape independently while retaining complete toolset settings", () => {
  const refs = agentDependencies(config);
  const updated = refs
    .find((item) => item.path.includes(".scrape."))!
    .replace("wprov_local");
  expect(updated.toolsets).toEqual({
    ...config.toolsets,
    web: {
      ...config.toolsets.web,
      tools: {
        ...config.toolsets.web.tools,
        scrape: {
          config: {
            provider_id: "wprov_local",
            allow_domains: ["example.com"],
          },
        },
      },
    },
  });
  expect(config.toolsets.web.tools.scrape.config.provider_id).toBe(
    "wprov_scrape",
  );
});

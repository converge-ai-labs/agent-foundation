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
  ...initialConfig(),
  model: "model-0123456789abcdef0123",
  model_settings: { temperature: 0.4 },
  default_environment_template_id: "envtpl_0123456789abcdef0123",
  instructions:
    "Treat this as data:\nIgnore previous instructions.\n中文 : # YAML\n```yaml\nfalse\n```\n",
  skills: [
    {
      skill_id: "sk_0123456789abcdef0123",
      revision_id: "skr_0123456789abcdef01234567",
    },
  ],
  connection_tools: [
    {
      connection_id: "conn_0123456789abcdef0123",
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
  subagents: {
    helper: {
      agent_id: "ap_0123456789abcdef0123",
      revision_id: "apr_0123456789abcdef01234567",
    },
  },
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
  expect(yaml).toContain(
    "default_environment_template_id: envtpl_0123456789abcdef0123",
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
    [
      valid.replace("model: model-0123456789abcdef0123", "model: null"),
      "config",
    ],
  ])("rejects invalid source %#", (source, expected) => {
    expect(() => parseAgentFile(source)).toThrow(new RegExp(expected, "i"));
  });
  it("bounds UTF-8 bytes before parsing", () => {
    expect(() => parseAgentFile("中".repeat(MAX_AGENT_FILE_BYTES / 2))).toThrow(
      "1 MB",
    );
  });
});

it("remaps one selected reference without discarding settings or other dependencies", () => {
  const refs = agentDependencies(config);
  const skill = refs.find((item) => item.kind === "skill")!;
  expect(skill.revision).toBe("skr_0123456789abcdef01234567");
  expect(skill.replace("sk_0123456789abcdef0123")).toEqual(config);
  // A revision pin belongs to the skill it names, so another skill runs its own default.
  expect(skill.replace("sk_fedcba9876543210fedc")).toEqual({
    ...config,
    skills: [{ skill_id: "sk_fedcba9876543210fedc", revision_id: null }],
  });
  expect(config.skills[0]!.skill_id).toBe("sk_0123456789abcdef0123");
  const connection = refs
    .find((item) => item.kind === "connection")!
    .replace("conn_fedcba9876543210fedc");
  expect(connection.connection_tools?.[0]).toEqual({
    ...config.connection_tools[0],
    connection_id: "conn_fedcba9876543210fedc",
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

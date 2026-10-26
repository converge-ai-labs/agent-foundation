import { expect, test } from "vitest";
import {
  advancedConfig,
  buildConfig,
  initialConfig,
  sectionsOf,
} from "./configuration";

const model = "model-0123456789abcdef0123";

test("ordinary editing preserves hidden configuration and leaves omission distinct from null", () => {
  const original = {
    ...initialConfig(),
    model,
    lazy_environment: false,
    plugins: [{ plugin_key: "example", instance_name: "example", config: {} }],
  };
  const config = buildConfig(
    original,
    { model: original.model, instructions: "Updated instructions" },
    advancedConfig(original),
  );
  expect(config.plugins).toEqual(original.plugins);
  expect(config.instructions).toBe("Updated instructions");
  expect(config.lazy_environment).toBe(false);
  expect(config.output_spec).toBeUndefined();
  const advanced = JSON.parse(advancedConfig(original));
  advanced.output_spec = null;
  expect(
    buildConfig(original, { model: original.model }, JSON.stringify(advanced))
      .output_spec,
  ).toBeNull();
});

test("advanced fields cannot overwrite hidden or common fields", () => {
  const original = { ...initialConfig(), model };
  expect(() =>
    buildConfig(original, { model: original.model }, '{"plugins": []}'),
  ).toThrow(/dedicated field/);
  expect(() =>
    buildConfig(
      original,
      { model: original.model },
      '{"instructions": "hidden override"}',
    ),
  ).toThrow(/dedicated field/);
  expect(() =>
    buildConfig(original, { model: original.model }, '{"reviewer": null}'),
  ).toThrow(/dedicated field/);
});

test("keeps reviewer and disabled tool configuration through dedicated fields", () => {
  const original = {
    ...initialConfig(),
    model,
    reviewer: {
      model: "model-0123456789abcdef0123",
      risk_threshold: "high" as const,
    },
    toolsets: {
      web: {
        enabled: false,
        tools: {
          search: {
            enabled: false,
            permission: "ask" as const,
            config: { max_results: 7 },
          },
        },
      },
    },
  };
  expect(JSON.parse(advancedConfig(original))).not.toHaveProperty("reviewer");
  const next = buildConfig(
    original,
    {
      model: original.model,
      reviewer: original.reviewer,
      toolsets: original.toolsets,
    },
    advancedConfig(original),
  );
  expect(next.reviewer).toEqual(original.reviewer);
  expect(next.toolsets).toEqual(original.toolsets);
});

test("schema validation reports the invalid advanced field", () => {
  const original = { ...initialConfig(), model };
  const advanced = {
    ...JSON.parse(advancedConfig(original)),
    retries: { tools: -1 },
  };
  expect(() =>
    buildConfig(original, { model }, JSON.stringify(advanced)),
  ).toThrow(/retries\/tools/);
});

test("media understanding is a dedicated field kept out of advanced configuration", () => {
  const original = {
    ...initialConfig(),
    model,
    media_understanding: {
      image: "model-fedcba9876543210fedc",
      video: null,
      audio: null,
    },
  };
  expect(JSON.parse(advancedConfig(original))).not.toHaveProperty(
    "media_understanding",
  );
  expect(
    buildConfig(
      original,
      {
        model: original.model,
        media_understanding: original.media_understanding,
      },
      advancedConfig(original),
    ).media_understanding,
  ).toEqual(original.media_understanding);
  expect(
    buildConfig(original, { model: original.model }, advancedConfig(original))
      .media_understanding,
  ).toBeUndefined();
  expect(() =>
    buildConfig(
      original,
      { model: original.model },
      '{"media_understanding": {"image": "model-fedcba9876543210fedc"}}',
    ),
  ).toThrow(/dedicated field/);
});

test("session memory stays in its dedicated section when viewing and saving configuration", () => {
  const original = {
    ...initialConfig(),
    model,
    memory: { enabled: false, spaces: [] },
    memory_mounts: [],
  };
  expect(sectionsOf(["memory", "memory_mounts"])).toEqual(new Set(["memory"]));
  const advanced = JSON.parse(advancedConfig(original));
  expect(advanced).not.toHaveProperty("memory");
  expect(advanced).not.toHaveProperty("memory_mounts");
  expect(
    buildConfig(
      original,
      { model, memory: original.memory },
      advancedConfig(original),
    ).memory,
  ).toEqual(original.memory);
  expect(() =>
    buildConfig(
      original,
      { model },
      JSON.stringify({ memory: original.memory }),
    ),
  ).toThrow(/dedicated field/);
});

test("skill synchronization stays in advanced configuration when editing selections", () => {
  const original = {
    ...initialConfig(),
    model,
    skills: [],
    skill_sync: { mode: "isolated" as const, cache_count: 7 },
  };
  const config = buildConfig(
    original,
    { model, skills: [{ skill_id: "sk_0123456789abcdef01234567" }] },
    advancedConfig(original),
  );
  expect(config.skill_sync).toEqual(original.skill_sync);
  expect(config.skills).toHaveLength(1);
  expect(
    buildConfig(original, { model }, '{"skill_sync":{"mode":"shared"}}')
      .skill_sync,
  ).toEqual({ mode: "shared" });
  expect(() =>
    buildConfig(
      original,
      { model },
      '{"skill_sync":{"mode":"shared","cache_count":7}}',
    ),
  ).toThrow(/skill_sync/);
});

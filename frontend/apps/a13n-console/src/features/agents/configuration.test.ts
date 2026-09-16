import { expect, test } from "vitest";
import { advancedConfig, buildConfig, initialConfig } from "./configuration";

test("ordinary editing preserves hidden configuration and leaves omission distinct from null", () => {
  const original = {
    ...initialConfig("Support"),
    model: { model_key: "support" },
    secret_requirements: [{ key: "support-token", required: true }],
  };
  const config = buildConfig(
    original,
    { model: original.model, instructions: "Updated instructions" },
    advancedConfig(original),
  );
  expect(config.secret_requirements).toEqual(original.secret_requirements);
  expect(config.instructions).toBe("Updated instructions");
  expect(config.output_spec).toBeUndefined();
  const advanced = JSON.parse(advancedConfig(original));
  advanced.output_spec = null;
  expect(
    buildConfig(original, { model: original.model }, JSON.stringify(advanced))
      .output_spec,
  ).toBeNull();
});

test("advanced fields cannot overwrite hidden or common fields", () => {
  const original = initialConfig("Support");
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
    ...initialConfig("Support"),
    model: { model_key: "support" },
    reviewer: {
      model: "mdl_0123456789abcdef",
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
  const original = initialConfig("Support");
  const advanced = {
    ...JSON.parse(advancedConfig(original)),
    retries: { tools: -1 },
  };
  expect(() =>
    buildConfig(
      original,
      { model: { model_key: "support" } },
      JSON.stringify(advanced),
    ),
  ).toThrow(/retries\/tools/);
});

test("memory is a dedicated revision field preserving explicit false, zero and disable", () => {
  const original = {
    ...initialConfig("Support"),
    model: { model_key: "support" },
    memory: {
      provider_id: "memprov_0123456789abcdef",
      scope: "agent" as const,
      auto_recall: false,
      recall_threshold: 0,
      toolset: false,
    },
  };
  expect(JSON.parse(advancedConfig(original))).not.toHaveProperty("memory");
  const updated = buildConfig(
    original,
    { model: original.model, memory: original.memory },
    advancedConfig(original),
  );
  expect(updated.memory).toEqual(original.memory);
  expect(
    buildConfig(
      original,
      { model: original.model, memory: null },
      advancedConfig(original),
    ).memory,
  ).toBeNull();
  expect(() =>
    buildConfig(original, { model: original.model }, '{"memory": null}'),
  ).toThrow(/dedicated field/);
  expect(() =>
    buildConfig(
      original,
      {
        model: original.model,
        memory: { ...original.memory, recall_timeout: 0 },
      },
      advancedConfig(original),
    ),
  ).toThrow(/recall_timeout/);
});

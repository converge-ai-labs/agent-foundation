import { expect, test } from "vitest";
import { advancedConfig, buildConfig, initialConfig } from "./configuration";

const model = { model_id: "mdl_0123456789abcdef0123" };

test("ordinary editing preserves hidden configuration and leaves omission distinct from null", () => {
  const original = {
    ...initialConfig(),
    model,
    secret_requirements: [{ key: "support_token", scope: "user" as const }],
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
      model: "mdl_0123456789abcdef0123",
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
      image: "mdl_fedcba9876543210fedc",
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
      '{"media_understanding": {"image": "mdl_fedcba9876543210fedc"}}',
    ),
  ).toThrow(/dedicated field/);
});

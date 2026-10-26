import { act, renderHook } from "@testing-library/react";
import { expect, it } from "vitest";
import { initialConfig } from "../configuration";
import { buildDraftConfig, thinkingEfforts, useAgentDraft } from "./draft";

const asIs = (value: string) => value;
const vision = "model-00000000000000000001",
  motion = "model-00000000000000000002";

function draftFor(config: Partial<ReturnType<typeof initialConfig>> = {}) {
  const initial = {
    ...initialConfig(),
    model: "model-0123456789abcdef0123",
    ...config,
  };
  return renderHook(() => useAgentDraft(initial)).result;
}

it("edits builtin skill selections without losing workspace pins or changing tools", () => {
  const skill = {
    skill_id: "sk_0123456789abcdef0123",
    revision_id: "skr_0123456789abcdef0123",
  };
  const toolsets = { files: { enabled: false } };
  const draft = draftFor({
    skills: [skill],
    builtin_skills: ["platform-operations"],
    toolsets,
  });
  expect(draft.current.builtinSkills).toEqual(["platform-operations"]);
  expect(draft.current.advanced).not.toContain("builtin_skills");
  expect(draft.current.dirty).toBe(false);
  act(() => draft.current.setBuiltinSkills([]));
  expect(draft.current.dirty).toBe(true);
  expect(buildDraftConfig(draft.current, undefined, asIs)).toMatchObject({
    ok: true,
    config: { builtin_skills: [], skills: [skill], toolsets },
  });
  act(() => draft.current.setBuiltinSkills(["platform-operations"]));
  expect(draft.current.dirty).toBe(false);
  expect(buildDraftConfig(draft.current, undefined, asIs)).toMatchObject({
    ok: true,
    config: {
      builtin_skills: ["platform-operations"],
      skills: [skill],
      toolsets,
    },
  });
  act(() => draft.current.setAdvanced('{"builtin_skills": []}'));
  expect(buildDraftConfig(draft.current, undefined, asIs).ok).toBe(false);
});

it("seeds media understanding from the saved configuration and publishes edits", () => {
  const draft = draftFor({
    media_understanding: { image: vision, video: null, audio: null },
  });
  expect(draft.current.mediaUnderstanding).toEqual({
    image: vision,
    video: null,
    audio: null,
  });
  expect(draft.current.dirty).toBe(false);
  act(() =>
    draft.current.setMediaUnderstanding({
      image: vision,
      video: motion,
      audio: null,
    }),
  );
  expect(draft.current.dirty).toBe(true);
  const built = buildDraftConfig(draft.current, undefined, asIs);
  expect(built.ok && built.config.media_understanding).toEqual({
    image: vision,
    video: motion,
    audio: null,
  });
});

it("omits media understanding while every kind inherits", () => {
  const draft = draftFor();
  act(() =>
    draft.current.setMediaUnderstanding({
      image: vision,
      video: null,
      audio: null,
    }),
  );
  expect(draft.current.dirty).toBe(true);
  act(() =>
    draft.current.setMediaUnderstanding({
      image: null,
      video: null,
      audio: null,
    }),
  );
  expect(draft.current.dirty).toBe(false);
  const built = buildDraftConfig(draft.current, undefined, asIs);
  expect(built.ok).toBe(true);
  expect(built.ok && "media_understanding" in built.config).toBe(false);
});

// The shape the Service derives from the calling API's Pydantic AI settings.
const settingsSchema = {
  type: "object",
  additionalProperties: false,
  properties: {
    max_tokens: { type: "integer" },
    temperature: { type: "number" },
    thinking: {
      anyOf: [
        { type: "boolean" },
        { enum: ["minimal", "low", "medium", "high"], type: "string" },
      ],
    },
  },
};

it("reads the thinking efforts a calling API allows", () => {
  expect(thinkingEfforts(settingsSchema)).toEqual([
    "minimal",
    "low",
    "medium",
    "high",
  ]);
  expect(thinkingEfforts(undefined)).toEqual([]);
  expect(thinkingEfforts({ properties: { thinking: true } })).toEqual([]);
});

it("checks model settings against the calling API's schema before publishing", () => {
  const draft = draftFor();
  act(() => {
    draft.current.setThinking("high");
    draft.current.setSettings('{"temperature": 0.2}');
  });
  const built = buildDraftConfig(draft.current, settingsSchema, asIs);
  expect(built.ok && built.config.model_settings).toEqual({
    temperature: 0.2,
    thinking: "high",
  });
  act(() => draft.current.setSettings('{"seed": 7}'));
  const refused = buildDraftConfig(draft.current, settingsSchema, asIs);
  expect(refused.ok).toBe(false);
  expect(!refused.ok && refused.stage).toBe("model");
  expect(!refused.ok && refused.error.message).toMatch(
    /must NOT have additional properties/,
  );
});

it("carries model characteristics through advanced JSON", () => {
  const characteristics = {
    context_window_tokens: 200_000,
    compact_threshold: 0.8,
  };
  const draft = draftFor({ model_characteristics: characteristics });
  expect(JSON.parse(draft.current.advanced).model_characteristics).toEqual(
    characteristics,
  );
  const built = buildDraftConfig(draft.current, undefined, asIs);
  expect(built.ok && built.config.model_characteristics).toEqual(
    characteristics,
  );
  act(() =>
    draft.current.setAdvanced(
      '{"model_characteristics": {"context_window_tokens": 100000}}',
    ),
  );
  expect(draft.current.dirty).toBe(true);
  const edited = buildDraftConfig(draft.current, undefined, asIs);
  expect(edited.ok && edited.config.model_characteristics).toEqual({
    context_window_tokens: 100_000,
  });
});

it("publishes default memory mounts and keeps them out of advanced JSON", () => {
  const handbook = {
    name: "handbook",
    memory_id: "mem_0123456789abcdef0123",
    access: "read" as const,
  };
  const draft = draftFor({ memory_mounts: [handbook] });
  expect(draft.current.memoryMounts).toEqual([handbook]);
  expect(draft.current.advanced).not.toContain("memory_mounts");
  expect(draft.current.dirty).toBe(false);
  act(() =>
    draft.current.setMemoryMounts((previous) => [
      ...previous,
      { name: "prefs", memory_id: "mem_fedcba9876543210fedc", access: "write" },
    ]),
  );
  expect(draft.current.dirty).toBe(true);
  const built = buildDraftConfig(draft.current, undefined, asIs);
  expect(built.ok && built.config.memory_mounts).toEqual([
    handbook,
    { name: "prefs", memory_id: "mem_fedcba9876543210fedc", access: "write" },
  ]);
  act(() => draft.current.setAdvanced('{"memory_mounts": []}'));
  const restated = buildDraftConfig(draft.current, undefined, asIs);
  expect(restated.ok).toBe(false);
});

it("preserves raw settings and distinguishes inheritance from explicit clearing", () => {
  const settings = {
    extra_body: { reasoning: { effort: "future" } },
    extra_headers: { "x-experiment": "on" },
  };
  const draft = draftFor({ model: vision, model_settings: settings });
  expect(draft.current.thinking).toBe("default");
  let built = buildDraftConfig(draft.current, undefined, asIs);
  expect(built.ok && built.config.model_settings).toEqual(settings);
  act(() =>
    draft.current.setSettings('{"extra_body": {}, "extra_headers": {}}'),
  );
  built = buildDraftConfig(draft.current, undefined, asIs);
  expect(built.ok && built.config.model_settings).toEqual({
    extra_body: {},
    extra_headers: {},
  });
  act(() => draft.current.setSettings("{}"));
  built = buildDraftConfig(draft.current, undefined, asIs);
  expect(built.ok && built.config.model_settings).toEqual({});
  act(() => draft.current.setThinking("false"));
  built = buildDraftConfig(draft.current, undefined, asIs);
  expect(built.ok && built.config.model_settings).toEqual({ thinking: false });
});

it("publishes session memory defaults independently of shared mounts", () => {
  const memory = {
    enabled: true,
    spaces: [{ name: "team", space_id: "msp_00000000000000000001" }],
  };
  const reviewer = { model: "review-model" };
  const draft = draftFor({ memory, reviewer });
  expect(draft.current.memory).toEqual(memory);
  expect(JSON.parse(draft.current.advanced)).not.toHaveProperty("memory");
  expect(draft.current.dirty).toBe(false);
  act(() => draft.current.setMemory({ ...memory, enabled: false }));
  expect(draft.current.dirty).toBe(true);
  const built = buildDraftConfig(draft.current, undefined, asIs);
  expect(built.ok && built.config.memory).toEqual({
    ...memory,
    enabled: false,
  });
  expect(built.ok && built.config.memory_mounts).toEqual([]);
  expect(built.ok && built.config.reviewer).toEqual(reviewer);
  act(() =>
    draft.current.setMemory({
      enabled: true,
      spaces: [{ name: "Invalid name", space_id: "msp_00000000000000000001" }],
    }),
  );
  const invalid = buildDraftConfig(draft.current, undefined, asIs);
  expect(invalid.ok).toBe(false);
  expect(!invalid.ok && invalid.stage).toBe("advanced");
  expect(!invalid.ok && invalid.error.message).toBe(
    "Use unique mount names: lowercase letters, digits and hyphens, starting with a letter.",
  );
});

it("requires a reviewer for declared builtin and connection review permissions", () => {
  for (const config of [
    {
      toolsets: {
        shell: {
          enabled: true,
          tools: { exec: { permission: "review" as const } },
        },
      },
    },
    {
      connection_tools: [
        {
          connection_id: "con_0123456789abcdef0123",
          permission: "review" as const,
        },
      ],
    },
    {
      connection_tools: [
        {
          connection_id: "con_0123456789abcdef0123",
          permissions: { send: "review" as const },
        },
      ],
    },
  ]) {
    const draft = draftFor(config);
    const refused = buildDraftConfig(draft.current, undefined, asIs);
    expect(refused.ok).toBe(false);
    expect(!refused.ok && refused.stage).toBe("reviewer");
    act(() => draft.current.setReviewer({ model: "review-model" }));
    const built = buildDraftConfig(draft.current, undefined, asIs);
    expect(built.ok && built.config.reviewer).toEqual({
      model: "review-model",
    });
    expect(draft.current.dirty).toBe(true);
  }
});

it("preserves reviewer fields and rules, and includes only intentional edits", () => {
  const reviewer = {
    model: "review-model",
    instruction: "Inspect data access",
    shell_instruction: "Inspect shell operations",
    on_error: "allow" as const,
    timeout_seconds: 45.5,
    model_settings: { extra_body: { policy: "strict" } },
    rules: {
      "mcp/source/*": { risk_threshold: "low" as const },
      "*": { on_flagged: "approval_required" as const },
    },
  };
  const draft = draftFor({ reviewer });
  expect(draft.current.dirty).toBe(false);
  expect(buildDraftConfig(draft.current, undefined, asIs)).toMatchObject({
    ok: true,
    config: { reviewer },
  });
  act(() => draft.current.setReviewer({ ...reviewer, risk_threshold: "high" }));
  expect(draft.current.dirty).toBe(true);
  expect(buildDraftConfig(draft.current, undefined, asIs)).toMatchObject({
    ok: true,
    config: { reviewer: { ...reviewer, risk_threshold: "high" } },
  });
  act(() => draft.current.setReviewer(reviewer));
  expect(draft.current.dirty).toBe(false);
});

it("toggles discovery while preserving proxy settings", () => {
  const id = "con_0123456789abcdef0123";
  const draft = draftFor({
    connection_tools: [{ connection_id: id }],
    tool_search: {
      groups: { knowledge: { description: "Lookup", connections: [id] } },
      config: { mode: "proxy", max_results: 3 },
    },
  });
  expect(draft.current.dirty).toBe(false);
  act(() =>
    draft.current.setToolSearch((current) => ({ ...current, mode: "direct" })),
  );
  const directConfig = buildDraftConfig(draft.current, undefined, asIs);
  expect(directConfig.ok && directConfig.config.tool_search).toBeUndefined();
  act(() =>
    draft.current.setToolSearch((current) => ({ ...current, mode: "proxy" })),
  );
  const built = buildDraftConfig(draft.current, undefined, asIs);
  expect(built.ok && built.config.tool_search).toMatchObject({
    config: { mode: "proxy", max_results: 3 },
  });
  act(() => draft.current.setConnections([]));
  const removed = buildDraftConfig(draft.current, undefined, asIs);
  expect(
    removed.ok && removed.config.tool_search?.groups?.knowledge.connections,
  ).toEqual([]);
  act(() =>
    draft.current.setToolSearch((current) => ({ ...current, mode: "direct" })),
  );
  const direct = buildDraftConfig(draft.current, undefined, asIs);
  expect(direct.ok && direct.config.tool_search).toBeUndefined();
});

it("reports invalid group names in the discovery section", () => {
  const draft = draftFor();
  act(() =>
    draft.current.setToolSearch({
      mode: "proxy",
      groups: [{ name: "bad__group", description: "Lookup" }],
    }),
  );
  const built = buildDraftConfig(draft.current, undefined, asIs);
  expect(built.ok).toBe(false);
  expect(!built.ok && built.stage).toBe("toolSearch");
});

it("publishes sparse execution defaults and removes them when reset to inherit", () => {
  const draft = draftFor({
    execution: {
      tools: { mode: "auto", foreground_wait_seconds: 5 },
      steering: "immediate",
    },
  });
  expect(draft.current.dirty).toBe(false);
  act(() =>
    draft.current.setExecution({
      tools: { mode: "inline", foreground_wait_seconds: 0 },
      steering: "boundary",
      environment: { APP_MODE: "run", EMPTY: "" },
    }),
  );
  const built = buildDraftConfig(draft.current, undefined, asIs);
  expect(built.ok && built.config.execution).toEqual({
    tools: { mode: "inline", foreground_wait_seconds: 0 },
    steering: "boundary",
    environment: { APP_MODE: "run", EMPTY: "" },
  });
  expect(draft.current.dirty).toBe(true);
  act(() =>
    draft.current.setExecution({
      tools: { mode: null, foreground_wait_seconds: null },
      steering: null,
    }),
  );
  const inherited = buildDraftConfig(draft.current, undefined, asIs);
  expect(inherited.ok && inherited.config.execution).toBeUndefined();
});

it("saves plugin edits and updates only the renamed or removed discovery memberships", () => {
  const plugin = {
    instance_name: "audit",
    plugin_key: "test.audit",
    config: { nested: { retained: true } },
  };
  const other = {
    instance_name: "other",
    plugin_key: "test.audit",
    config: {},
  };
  const draft = draftFor({
    plugins: [plugin, other],
    tool_search: {
      groups: {
        lookup: {
          description: "Lookup",
          plugins: ["audit", "other"],
          capabilities: ["environment"],
        },
      },
      config: { mode: "proxy" },
    },
  });
  expect(draft.current.dirty).toBe(false);
  act(() =>
    draft.current.applyPlugin({ ...plugin, instance_name: "renamed" }, "audit"),
  );
  expect(draft.current.dirty).toBe(true);
  let built = buildDraftConfig(draft.current, undefined, asIs);
  expect(built).toMatchObject({
    ok: true,
    config: {
      plugins: [{ ...plugin, instance_name: "renamed" }, other],
      tool_search: {
        groups: {
          lookup: {
            plugins: ["renamed", "other"],
            capabilities: ["environment"],
          },
        },
      },
    },
  });
  act(() => draft.current.removePlugin("renamed"));
  built = buildDraftConfig(draft.current, undefined, asIs);
  expect(built).toMatchObject({
    ok: true,
    config: {
      plugins: [other],
      tool_search: {
        groups: {
          lookup: { plugins: ["other"], capabilities: ["environment"] },
        },
      },
    },
  });
  expect(draft.current.initial.plugins).toEqual([plugin, other]);
});

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

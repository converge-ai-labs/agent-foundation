import { act, renderHook } from "@testing-library/react";
import { expect, it } from "vitest";
import { initialConfig } from "../configuration";
import { buildDraftConfig, useAgentDraft } from "./draft";

const asIs = (value: string) => value;

function draftFor(config: Partial<ReturnType<typeof initialConfig>> = {}) {
  const initial = {
    ...initialConfig("Media"),
    model: { model_key: "primary" },
    ...config,
  };
  return renderHook(() => useAgentDraft(initial)).result;
}

it("seeds media understanding from the saved configuration and publishes edits", () => {
  const draft = draftFor({
    media_understanding: { image: "vision", video: null, audio: null },
  });
  expect(draft.current.mediaUnderstanding).toEqual({
    image: "vision",
    video: null,
    audio: null,
  });
  expect(draft.current.dirty).toBe(false);
  act(() =>
    draft.current.setMediaUnderstanding({
      image: "vision",
      video: "motion",
      audio: null,
    }),
  );
  expect(draft.current.dirty).toBe(true);
  const built = buildDraftConfig(draft.current, undefined, asIs);
  expect(built.ok && built.config.media_understanding).toEqual({
    image: "vision",
    video: "motion",
    audio: null,
  });
});

it("omits media understanding while every kind inherits", () => {
  const draft = draftFor();
  act(() =>
    draft.current.setMediaUnderstanding({
      image: "vision",
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

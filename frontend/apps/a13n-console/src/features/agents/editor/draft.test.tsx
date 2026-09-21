import { act, renderHook } from "@testing-library/react";
import { expect, it } from "vitest";
import { initialConfig } from "../configuration";
import { buildDraftConfig, useAgentDraft } from "./draft";

it("publishes media choices with the Agent draft and restores clean inheritance", () => {
  const initial = {
    ...initialConfig("Media"),
    model: { model_key: "primary" },
  };
  const { result } = renderHook(() => useAgentDraft(initial));
  expect(result.current.dirty).toBe(false);
  act(() => result.current.setMediaUnderstanding({ image: "vision" }));
  expect(result.current.dirty).toBe(true);
  const built = buildDraftConfig(result.current, undefined, (value) => value);
  expect(built.ok).toBe(true);
  if (built.ok)
    expect(built.config.media_understanding).toEqual({ image: "vision" });
  act(() => result.current.setMediaUnderstanding({ image: null }));
  expect(result.current.dirty).toBe(false);
});

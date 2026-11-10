// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import { useSyncExternalStore } from "react";
import * as Y from "yjs";
import { ThreadDraft } from "./draft";
import { useComposerRunChoices } from "./composer-run-choices";

afterEach(cleanup);

it.each([
  { agent: "reviewer", inheritedModel: "original" },
  { agent: "writer", inheritedModel: "new-default" },
])(
  "preserves remote choices while configuration is stale: %j",
  async (fresh) => {
    const a = new ThreadDraft();
    const b = new ThreadDraft();
    let resolve!: (value: typeof fresh) => void;
    const refresh = vi.fn(
      () =>
        new Promise<typeof fresh>((done) => {
          resolve = done;
        }),
    );
    const { result, rerender } = renderHook(
      (configuration) => {
        useSyncExternalStore(b.subscribe, b.getSnapshot);
        return useComposerRunChoices(
          b,
          configuration.agent,
          configuration.inheritedModel,
          refresh,
        );
      },
      { initialProps: { agent: "writer", inheritedModel: "original" } },
    );
    a.modelOptions = {
      agent: fresh.agent,
      effective_model: fresh.inheritedModel,
      thinking: "high",
    };
    act(() => Y.applyUpdate(b.optionsDoc, Y.encodeStateAsUpdate(a.optionsDoc)));
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(result.current.pending).toBe(true);
    expect(b.controls.thinking).toBe("high");
    await act(async () => {
      resolve(fresh);
    });
    rerender(fresh);
    Y.applyUpdate(a.optionsDoc, Y.encodeStateAsUpdate(b.optionsDoc));
    expect(a.controls.thinking).toBe("high");
    expect(result.current.pending).toBe(false);
    a.optionsDoc.destroy();
    b.optionsDoc.destroy();
  },
);

it("clears a confirmed obsolete binding without deleting a concurrent replacement", async () => {
  const draft = new ThreadDraft();
  draft.modelOptions = {
    agent: "writer",
    effective_model: "original",
    thinking: "low",
  };
  const fresh = { agent: "reviewer", inheritedModel: "original" };
  let resolve!: (value: typeof fresh) => void;
  const refresh = vi.fn(
    () =>
      new Promise<typeof fresh>((done) => {
        resolve = done;
      }),
  );
  renderHook(() => {
    useSyncExternalStore(draft.subscribe, draft.getSnapshot);
    return useComposerRunChoices(
      draft,
      fresh.agent,
      fresh.inheritedModel,
      refresh,
    );
  });
  await act(async () => {
    resolve(fresh);
  });
  expect(draft.modelOptions).toBeUndefined();
  act(() => {
    draft.modelOptions = {
      agent: "writer",
      effective_model: "original",
      thinking: "low",
    };
  });
  act(() => {
    draft.modelOptions = {
      agent: "reviewer",
      effective_model: "original",
      thinking: "high",
    };
  });
  await act(async () => {
    resolve(fresh);
  });
  expect(draft.controls.thinking).toBe("high");
  draft.optionsDoc.destroy();
});

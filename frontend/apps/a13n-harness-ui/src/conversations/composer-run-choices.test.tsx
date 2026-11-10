// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import { useSyncExternalStore } from "react";
import * as Y from "yjs";
import { encode, optionsReplica, ThreadDraft } from "./draft";
import { initializeLegacyRunChoices } from "./legacy-run-choices";
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

function ownerFrame(draft: ThreadDraft, options = optionsReplica()) {
  return {
    draft_id: "draft-legacy",
    participant_id: "owner",
    participants: {},
    update_base64: encode(Y.encodeStateAsUpdate(draft.doc)),
    options_update_base64: encode(Y.encodeStateAsUpdate(options)),
  };
}

it.each([undefined, "original"])(
  "imports legacy browser choices into initial shared state (override: %s)",
  (override) => {
    const draft = new ThreadDraft();
    const key = "a13n-harness-ui.run-choices.upgraded-thread";
    localStorage.setItem(
      key,
      JSON.stringify({
        agent: "writer",
        model: "original",
        override,
        controls: { thinking: "high", fast: false, reasoning_mode: "pro" },
      }),
    );
    const { unmount } = renderHook(() => {
      useSyncExternalStore(draft.subscribe, draft.getSnapshot);
      return useComposerRunChoices(
        draft,
        "writer",
        "original",
        undefined,
        "upgraded-thread",
      );
    });
    expect(draft.modelOptions).toBeUndefined();
    act(() => draft.receive(ownerFrame(draft)));
    expect(draft.modelOptions).toMatchObject({
      agent: "writer",
      effective_model: "original",
      thinking: "high",
      fast: false,
      reasoning_mode: "pro",
    });
    expect(draft.modelId).toBe(override);
    expect(draft.synchronized).toBe(false);
    expect(localStorage.getItem(key)).not.toBeNull();
    act(() => draft.receive(ownerFrame(draft, draft.optionsDoc)));
    expect(draft.synchronized).toBe(true);
    expect(localStorage.getItem(key)).toBeNull();
    unmount();
  },
);

it.each([false, true])(
  "never replaces saved shared options or an explicit reset with legacy choices (reset: %s)",
  (reset) => {
    const draft = new ThreadDraft();
    const saved = optionsReplica();
    saved.getMap("options").set("model", {
      agent: "writer",
      effective_model: "original",
      thinking: "low",
    });
    if (reset) saved.getMap("options").delete("model");
    draft.receive(ownerFrame(draft, saved));
    const key = "a13n-harness-ui.run-choices.existing-thread";
    localStorage.setItem(
      key,
      JSON.stringify({
        agent: "writer",
        model: "original",
        controls: { thinking: "high" },
      }),
    );
    initializeLegacyRunChoices("existing-thread", draft, "writer", "original");
    expect(draft.controls.thinking).toBe(reset ? undefined : "low");
    expect(draft.synchronized).toBe(true);
    expect(localStorage.getItem(key)).toBeNull();
  },
);

it("waits for matching Agent/defaults and ignores malformed legacy records", () => {
  const draft = new ThreadDraft();
  draft.receive(ownerFrame(draft));
  const key = "a13n-harness-ui.run-choices.waiting-thread";
  localStorage.setItem(
    key,
    JSON.stringify({
      agent: "writer",
      model: "original",
      controls: { thinking: "high" },
    }),
  );
  initializeLegacyRunChoices("waiting-thread", draft, undefined, undefined);
  initializeLegacyRunChoices("waiting-thread", draft, "reviewer", "original");
  initializeLegacyRunChoices("waiting-thread", draft, "writer", "other");
  expect(draft.modelOptions).toBeUndefined();
  localStorage.setItem(key, "not json");
  initializeLegacyRunChoices("waiting-thread", draft, "writer", "original");
  expect(draft.modelOptions).toBeUndefined();
  localStorage.removeItem(key);
});

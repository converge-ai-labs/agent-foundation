import { useLayoutEffect } from "react";
import type { ThreadDraft } from "./draft";
import type { ModelControlValues } from "./model-controls";
import { initializeLegacyRunChoices } from "./legacy-run-choices";

type Configuration = {
  agent: string | undefined;
  inheritedModel: string | null | undefined;
};

/** Composer choices live in the shared document, never browser preferences. */
export function useComposerRunChoices(
  draft: ThreadDraft,
  agent: string | undefined,
  inheritedModel: string | null | undefined,
  refreshConfiguration?: () => Promise<Configuration | undefined>,
  legacyThreadId?: string,
) {
  const saved = draft.modelOptions;
  const savedKey = JSON.stringify(saved);
  useLayoutEffect(() => {
    if (legacyThreadId)
      initializeLegacyRunChoices(legacyThreadId, draft, agent, inheritedModel);
  }, [
    draft,
    legacyThreadId,
    agent,
    inheritedModel,
    savedKey,
    draft.synchronized,
  ]);
  const mismatch = (configuration: Configuration) =>
    !!saved?.agent &&
    !!configuration.agent &&
    (saved.agent !== configuration.agent ||
      ((saved.model_id || configuration.inheritedModel !== undefined) &&
        (saved.model_id ?? configuration.inheritedModel) !==
          saved.effective_model));
  const mismatched = mismatch({ agent, inheritedModel });
  useLayoutEffect(() => {
    if (!mismatched) return;
    if (!refreshConfiguration) {
      draft.modelOptions = undefined;
      return;
    }
    // A draft update can arrive before the matching Thread detail refresh.
    // Verify against a fresh configuration before deleting shared choices.
    let cancelled = false;
    void refreshConfiguration().then((configuration) => {
      if (
        !cancelled &&
        configuration &&
        JSON.stringify(draft.modelOptions) === savedKey &&
        mismatch(configuration)
      )
        draft.modelOptions = undefined;
    });
    return () => {
      cancelled = true;
    };
  }, [
    draft,
    agent,
    inheritedModel,
    savedKey,
    mismatched,
    refreshConfiguration,
  ]);

  function choose(modelId: string | undefined, controls: ModelControlValues) {
    draft.modelOptions =
      modelId || Object.values(controls).some((value) => value != null)
        ? {
            model_id: modelId,
            agent,
            effective_model: modelId ?? inheritedModel ?? undefined,
            ...controls,
          }
        : undefined;
  }
  return {
    pending:
      !!saved?.agent &&
      (!agent ||
        (!saved.model_id && inheritedModel === undefined) ||
        (!!refreshConfiguration && mismatched)),
    onControlsChange: (controls: ModelControlValues) =>
      choose(draft.modelId, controls),
    onModelChange: (next: string | undefined) =>
      choose(
        next,
        (next ?? inheritedModel) === (draft.modelId ?? inheritedModel)
          ? draft.controls
          : {},
      ),
  };
}

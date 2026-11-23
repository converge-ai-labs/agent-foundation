import { useState, useSyncExternalStore } from "react";
import type { ThreadDraft } from "./draft";

const visible = () => document.visibilityState === "visible";
function subscribeVisibility(listener: () => void) {
  document.addEventListener("visibilitychange", listener);
  return () => document.removeEventListener("visibilitychange", listener);
}

export function emptyForPrediction(draft: ThreadDraft) {
  return draft.doc.getText("text").length === 0 && !draft.annotations.length;
}

export function useInputPrediction(
  draft: ThreadDraft,
  continuationId: string | null | undefined,
  eligible: boolean,
) {
  const foreground = useSyncExternalStore(subscribeVisibility, visible);
  const [composing, setComposing] = useState(false);
  const active =
    eligible &&
    foreground &&
    !composing &&
    emptyForPrediction(draft) &&
    !!continuationId &&
    draft.dismissedPrediction !== continuationId;
  const cached = draft.prediction;
  const text =
    cached?.continuation_id === continuationId ? cached?.text : undefined;
  return {
    prediction:
      active && text
        ? {
            text,
            dismiss: () => {
              draft.dismissedPrediction = continuationId ?? undefined;
              draft.notify();
            },
          }
        : undefined,
    setComposing,
  };
}

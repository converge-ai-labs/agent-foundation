import { useEffect, useState, useSyncExternalStore } from "react";
import { result, type Transport } from "../transport/client";
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
  transport: Transport,
  draft: ThreadDraft,
  threadId: string,
  continuationId: string | null | undefined,
  eligible: boolean,
) {
  const foreground = useSyncExternalStore(subscribeVisibility, visible);
  const [composing, setComposing] = useState(false);
  const [abstained, setAbstained] = useState<string>();
  const key = continuationId ? `${threadId}/${continuationId}` : undefined;
  const active =
    eligible &&
    foreground &&
    !composing &&
    emptyForPrediction(draft) &&
    !!key &&
    draft.dismissedPrediction !== continuationId;
  const cached = draft.prediction;
  const text =
    cached?.continuation_id === continuationId ? cached?.text : undefined;
  useEffect(() => {
    if (!active || !continuationId || text || abstained === key) return;
    const controller = new AbortController();
    const unsubscribe = draft.subscribe(() => {
      if (
        !emptyForPrediction(draft) ||
        draft.replacement ||
        draft.submission.kind === "pending" ||
        draft.submission.kind === "unknown"
      ) {
        controller.abort();
      }
    });
    const visibilityChanged = () => {
      if (!visible()) controller.abort();
    };
    document.addEventListener("visibilitychange", visibilityChanged);
    void result(
      transport.client.POST("/api/threads/{thread_id}/input-prediction", {
        params: { path: { thread_id: threadId } },
        body: { continuation_id: continuationId },
        signal: controller.signal,
      }),
    )
      .then((response) => {
        // Successful predictions arrive via the authoritative shared-draft frame.
        if (
          !controller.signal.aborted &&
          response.continuation_id === continuationId &&
          response.text === null
        ) {
          setAbstained(key);
        }
      })
      .catch(() => {
        /* Predictions never interrupt editing. */
      });
    return () => {
      controller.abort();
      unsubscribe();
      document.removeEventListener("visibilitychange", visibilityChanged);
    };
  }, [
    abstained,
    active,
    continuationId,
    draft,
    key,
    text,
    threadId,
    transport,
  ]);
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

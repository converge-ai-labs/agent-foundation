import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { ArrowCounterClockwise, CaretRight } from "@phosphor-icons/react";
import { useTransport } from "../transport/context";
import { ApiError, result } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import styles from "./composer-settings.module.css";

export function ClearContext({
  threadId,
  continuationId,
  disabled,
  onPendingChange,
  onCleared,
  reconcile,
}: {
  threadId: string;
  continuationId?: string | null;
  disabled: boolean;
  onPendingChange: (pending: boolean) => void;
  onCleared: () => void;
  reconcile: () => void;
}) {
  const { client } = useTransport();
  const queries = useQueryClient();
  // Confirm exactly the context the user opened, not a newer shared head.
  const [target, setTarget] = useState<string | null>(null);
  const clear = useMutation({
    mutationFn: (expected: string) =>
      result(
        client.POST("/api/threads/{thread_id}/clear-context", {
          params: { path: { thread_id: threadId } },
          body: { expected_continuation_id: expected },
        }),
      ),
    onMutate: () => onPendingChange(true),
    onSuccess: async (detail) => {
      const key = ["thread", threadId, "detail"];
      await queries.cancelQueries({ queryKey: key, exact: true });
      queries.setQueryData(key, detail);
      setTarget(null);
      onCleared();
    },
    onSettled: () => {
      onPendingChange(false);
      reconcile();
    },
  });
  const stale = !!target && target !== continuationId;
  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        className={styles.optionAction}
        aria-label="Clear context"
        title="Clear context · keep chat history and your draft"
        disabled={disabled || !continuationId || clear.isPending}
        loading={clear.isPending}
        onClick={() => {
          clear.reset();
          setTarget(continuationId!);
        }}
      >
        <ArrowCounterClockwise aria-hidden />
        <span className={styles.optionCopy}>
          <span>Clear context</span>
          <small>Keep history, files and draft</small>
        </span>
        <CaretRight aria-hidden size={14} />
      </Button>
      <ModalFrame
        open={!!target}
        onOpenChange={(open) => {
          if (!open && !clear.isPending) setTarget(null);
        }}
        title="Clear chat context?"
        description="Start the next message without previous model messages, notes, tasks, or other saved Agent working state. Pending questions and approvals are discarded. Chat history, files, chat settings, and your unsent draft are kept."
        closeLabel="Close confirmation"
        footer={
          <>
            <Button
              variant="outline"
              disabled={clear.isPending}
              onClick={() => setTarget(null)}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              loading={clear.isPending}
              disabled={disabled || stale || clear.isPending || !target}
              onClick={() => target && clear.mutate(target)}
            >
              Clear context
            </Button>
          </>
        }
      >
        <ErrorNotice error={clear.error} />
        {clear.error && !(clear.error instanceof ApiError) && (
          <p role="alert">
            The result could not be confirmed. Refresh the chat before trying
            again; the context may already have been cleared.
          </p>
        )}
        {stale && (
          <p role="alert">
            The chat changed. Close this dialog and review it before clearing
            context.
          </p>
        )}
      </ModalFrame>
    </>
  );
}

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { useTaskOptionsDialog } from "./task-options";
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
  useTaskOptionsDialog(!!target);
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
        <span>Clear context</span>
        <CaretRight aria-hidden size={14} />
      </Button>
      <ModalFrame
        open={!!target}
        onOpenChange={(open) => {
          if (!open && !clear.isPending) setTarget(null);
        }}
        title="Clear chat context?"
        description="Reset the agent's context, notes, tasks and pending requests. Chat history, files, settings and your draft are kept."
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
            Couldn't confirm the reset. The context may already be cleared.
            Refresh the chat before trying again.
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

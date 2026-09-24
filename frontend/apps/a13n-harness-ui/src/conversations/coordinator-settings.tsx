import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { refreshThreadLists } from "./queries";
import { refreshThread } from "./refresh";

export function canPromoteCoordinator(
  thread: Schema<"ThreadSummary">,
  pending: boolean,
) {
  return (
    thread.role === "ordinary" &&
    !!thread.configuration.project_id &&
    !thread.archived &&
    thread.root_activity.state === "inactive" &&
    !pending
  );
}

export function useCoordinatorMutation(thread: Schema<"ThreadSummary">) {
  const { client } = useTransport();
  const queries = useQueryClient();
  return useMutation({
    mutationFn: (autoFollowup?: boolean) =>
      autoFollowup === undefined
        ? result(
            client.POST("/api/threads/{thread_id}/coordinator", {
              params: { path: { thread_id: thread.thread_id } },
            }),
          )
        : result(
            client.PATCH("/api/threads/{thread_id}/coordinator", {
              params: { path: { thread_id: thread.thread_id } },
              body: { auto_followup: autoFollowup },
            }),
          ),
    onSuccess: (updated) => {
      queries.setQueryData<Schema<"ThreadDetail">>(
        ["thread", thread.thread_id, "detail"],
        (current) => (current ? { ...current, thread: updated } : current),
      );
    },
    onSettled: () => {
      refreshThread(queries, thread.thread_id, "lifecycle");
      void refreshThreadLists(queries);
    },
    retry: false,
  });
}

export function CoordinatorPromotion({
  open,
  close,
  mutation,
}: {
  open: boolean;
  close: () => void;
  mutation: ReturnType<typeof useCoordinatorMutation>;
}) {
  return (
    <ModalFrame
      open={open}
      onOpenChange={(value) => {
        if (!value) close();
      }}
      title="Make this conversation a Coordinator?"
      closeLabel="Cancel"
      description="This keeps its history and settings. A Coordinator manages its own workers in this Project; existing conversations are not adopted. This role cannot be reversed."
    >
      <ErrorNotice error={mutation.error} />
      <Button
        loading={mutation.isPending}
        onClick={() => mutation.mutate(undefined, { onSuccess: close })}
      >
        Make Coordinator
      </Button>
    </ModalFrame>
  );
}

export function CoordinatorSettings({
  thread,
  pending,
}: {
  thread: Schema<"ThreadSummary">;
  pending: boolean;
}) {
  const mutation = useCoordinatorMutation(thread);
  const [promoting, setPromoting] = useState(false);
  return (
    <>
      {thread.role === "coordinator" ? (
        <Button
          variant="outline"
          loading={mutation.isPending}
          onClick={() => mutation.mutate(!thread.auto_followup)}
        >
          {thread.auto_followup
            ? "Pause automatic follow-up"
            : "Enable automatic follow-up"}
        </Button>
      ) : thread.role === "ordinary" && thread.configuration.project_id ? (
        <Button
          variant="outline"
          disabled={!canPromoteCoordinator(thread, pending)}
          onClick={() => setPromoting(true)}
        >
          Make Coordinator
        </Button>
      ) : null}
      <ErrorNotice error={mutation.error} />
      <CoordinatorPromotion
        open={promoting}
        close={() => setPromoting(false)}
        mutation={mutation}
      />
    </>
  );
}

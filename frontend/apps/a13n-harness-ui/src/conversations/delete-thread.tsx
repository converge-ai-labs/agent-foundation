import { useContext } from "react";
import { useLocation, useNavigate } from "react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import { ErrorNotice } from "../shell/ui";
import { ComposerDrafts } from "./composer";
import { NewConversationDrafts, newConversationPath } from "./new-conversation";
import { conversationTitle } from "./local-input";
import { refreshThreadLists } from "./queries";
import { useUnsent } from "./unsent";

export function canDeleteThread(thread: Schema<"ThreadSummary">) {
  return (
    !thread.parent_thread_id &&
    !thread.memory_scope &&
    thread.configuration.agent_source.kind !== "live"
  );
}

export function DeleteThread({
  thread,
  close,
}: {
  thread: Schema<"ThreadSummary">;
  close: () => void;
}) {
  const transport = useTransport();
  const queries = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const composers = useContext(ComposerDrafts);
  const unsent = useUnsent();
  const newDrafts = useContext(NewConversationDrafts);
  const deletion = useMutation({
    mutationFn: () =>
      result(
        transport.client.DELETE("/api/threads/{thread_id}", {
          params: {
            path: { thread_id: thread.thread_id },
            query: { expected_version: thread.metadata_version },
          },
        }),
      ),
    onSuccess: async (deleted) => {
      for (const id of deleted) {
        newDrafts.discardDeleted(id);
        composers.delete(id);
        unsent.store?.forget(id);
        await queries.cancelQueries({ queryKey: ["thread", id] });
        queries.removeQueries({ queryKey: ["thread", id] });
      }
      close();
      if (
        deleted.some(
          (id) => location.pathname === `/threads/${encodeURIComponent(id)}`,
        )
      )
        navigate(newConversationPath(thread.configuration.project_id), {
          replace: true,
        });
      void refreshThreadLists(queries);
      void queries.invalidateQueries({ queryKey: ["unsent-drafts"] });
      void queries.invalidateQueries({ queryKey: ["unsent-threads"] });
      void queries.invalidateQueries({ queryKey: ["projects"] });
    },
    retry: false,
  });
  return (
    <ModalFrame
      open
      onOpenChange={(open) => {
        if (!open && !deletion.isPending) close();
      }}
      title="Delete chat?"
      closeLabel="Cancel deletion"
      description={`“${conversationTitle(thread)}” and its subagent history will be deleted for everyone. This cannot be undone. Project files and other chats are kept.`}
      footer={
        <>
          <Button
            variant="outline"
            disabled={deletion.isPending}
            onClick={close}
          >
            Cancel
          </Button>
          <Button
            variant="destructive"
            loading={deletion.isPending}
            disabled={thread.root_activity.state !== "inactive"}
            onClick={() => deletion.mutate()}
          >
            Delete chat
          </Button>
        </>
      }
    >
      <ErrorNotice error={deletion.error} />
    </ModalFrame>
  );
}

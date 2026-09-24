import { PlusIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, ifMatch, rowTag, type Schema } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { MemoryMountRows, MountForm } from "../memories/mounts";
import {
  conversationKeys,
  conversationQueries,
  invalidateConversation,
} from "./api";
import { isInteractive } from "./transcript/run-actions";

/**
 * The memories the Run's Thread mounts for its later Runs. A mount's access
 * and recall change in place; its memory changes by removing the name and
 * adding it again.
 */
export function ThreadMemoryMounts({ run }: { run: Schema["RunView"] }) {
  const { t } = useTranslation();
  const client = useClient();
  const { workspace, can } = useWorkspace();
  const cache = useQueryClient();
  const [open, setOpen] = useState(false);
  const thread = useQuery(
    conversationQueries(client, workspace.id).thread(run.thread_id),
  );
  const path = { workspace_id: workspace.id, thread_id: run.thread_id };
  const mounts = useQuery({
    // Mount edits change the Thread, so they refresh with it.
    queryKey: [
      ...conversationKeys(workspace.id).thread(run.thread_id),
      "memories",
    ],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace_id}/threads/{thread_id}/memories", {
          params: { path },
          signal,
        })
        .then(data)
        .then((page) => page.items),
  });
  const changed = () =>
    invalidateConversation(cache, workspace.id, {
      sessionId: run.session_id,
      threadId: run.thread_id,
    });
  // Every change names the Thread the reader saw.
  const add = useMutation({
    mutationFn: (mount: Schema["MemoryMount"]) =>
      client.http
        .POST(
          "/api/v1/workspaces/{workspace_id}/threads/{thread_id}/memories",
          {
            params: { path },
            headers: ifMatch(thread.data && rowTag(thread.data)),
            body: mount,
          },
        )
        .then(data),
    onSuccess: () => {
      void changed();
      setOpen(false);
    },
  });
  const update = useMutation({
    mutationFn: ({
      name,
      body,
    }: {
      name: string;
      body: Schema["MemoryMountUpdate"];
    }) =>
      client.http
        .PATCH(
          "/api/v1/workspaces/{workspace_id}/threads/{thread_id}/memories/{name}",
          {
            params: { path: { ...path, name } },
            headers: ifMatch(thread.data && rowTag(thread.data)),
            body,
          },
        )
        .then(data),
    onSuccess: () => void changed(),
  });
  const remove = useMutation({
    mutationFn: (name: string) =>
      client.http.DELETE(
        "/api/v1/workspaces/{workspace_id}/threads/{thread_id}/memories/{name}",
        {
          params: { path: { ...path, name } },
          headers: ifMatch(thread.data && rowTag(thread.data)),
        },
      ),
    onSuccess: () => void changed(),
  });
  const editable = can("run") && !!thread.data && isInteractive(thread.data);
  return (
    <section className="flex flex-col gap-3" aria-label={t("Memories")}>
      <div className="flex items-center justify-between gap-2">
        <h3>{t("Memories")}</h3>
        {editable && (
          <ModalFrame
            open={open}
            onOpenChange={(value) => {
              setOpen(value);
              add.reset();
            }}
            title={t("Add memory")}
            description={t(
              "Mounted memories apply to this thread's later runs. A run keeps the memories it started with.",
            )}
            closeLabel={t("Close")}
            trigger={
              <Button type="button" variant="outline" size="sm">
                <PlusIcon size={14} />
                {t("Add memory")}
              </Button>
            }
          >
            {open && (
              <MountForm
                mounts={mounts.data ?? []}
                label={t("Add memory")}
                pending={add.isPending}
                error={add.error}
                onSubmit={(mount) => add.mutate(mount)}
                onCancel={() => setOpen(false)}
              />
            )}
          </ModalFrame>
        )}
      </div>
      <ErrorNotice
        error={mounts.error ?? thread.error ?? update.error ?? remove.error}
      />
      {mounts.isPending ? (
        <Loading variant="list" rows={1} />
      ) : (
        mounts.data && (
          <MemoryMountRows
            mounts={mounts.data}
            empty={t("No memories.")}
            // Each change names the Thread version it read.
            disabled={update.isPending || remove.isPending || thread.isFetching}
            onAccessChange={
              editable
                ? (name, access) => update.mutate({ name, body: { access } })
                : undefined
            }
            onRecallChange={
              editable
                ? (name, recall) => update.mutate({ name, body: { recall } })
                : undefined
            }
            onRemove={editable ? (name) => remove.mutate(name) : undefined}
          />
        )
      )}
    </section>
  );
}

import { BrainIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, ifMatch, rowTag, type Schema } from "../../shared/api";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import {
  defaultSessionMemory,
  SessionMemoryFields,
} from "../memories/session-settings";
import { conversationQueries, invalidateConversation, runPath } from "./api";

type Settings = Schema["SessionMemorySettings"];

export function SessionMemory({ sessionId }: { sessionId: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <ModalFrame
      open={open}
      onOpenChange={setOpen}
      title={t("Session memory")}
      description={t(
        "Saved files and their organization, separate from the conversation.",
      )}
      closeLabel={t("Close")}
      size="lg"
      trigger={
        <Button variant="ghost" size="sm">
          <BrainIcon size={15} />
          {t("Memory")}
        </Button>
      }
    >
      {open && <MemoryContent sessionId={sessionId} />}
    </ModalFrame>
  );
}

function MemoryContent({ sessionId }: { sessionId: string }) {
  const { t } = useTranslation();
  const client = useClient();
  const cache = useQueryClient();
  const { workspace, can, basePath } = useWorkspace();
  const session = useQuery({
    ...conversationQueries(client, workspace.id).session(sessionId),
    refetchInterval: 5000,
  });
  const [draft, setDraft] = useState<{ value: Settings; tag: string }>();
  const [cursor, setCursor] = useState<string>();
  const history = useQuery({
    queryKey: ["session-memory-history", workspace.id, sessionId, cursor],
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/sessions/{session_id}/memory/organizations", {
          params: {
            path: { session_id: sessionId },
            query: { cursor, limit: 10 },
          },
          signal,
        })
        .then(data),
    refetchInterval: 5000,
  });
  const save = useMutation({
    mutationFn: (memory: Settings) =>
      client
        .workspace(workspace.id)
        .PATCH("/api/v1/sessions/{session_id}", {
          params: { path: { session_id: sessionId } },
          headers: ifMatch(draft?.tag),
          body: { memory },
        })
        .then(data),
    onSuccess: () => {
      setDraft(undefined);
      void invalidateConversation(cache, workspace.id, { sessionId });
    },
  });
  const owner = session.data;
  if (!owner)
    return (
      <>
        <ErrorNotice error={session.error} />
        <Loading variant="form" />
      </>
    );
  const value = draft?.value ?? owner.memory ?? defaultSessionMemory;
  const status = owner.memory_status;
  return (
    <div className="grid gap-6">
      <form
        className="grid gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate(value);
        }}
      >
        <SessionMemoryFields
          value={value}
          onChange={(value) =>
            setDraft({ value, tag: draft?.tag ?? rowTag(owner) })
          }
          disabled={!can("write") || save.isPending}
        />
        <ErrorNotice
          error={save.error ?? session.error}
          retry={() => void session.refetch()}
        />
        {draft && (
          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="ghost"
              onClick={() => setDraft(undefined)}
            >
              {t("Discard")}
            </Button>
            <Button
              type="submit"
              disabled={
                !can("write") ||
                save.isPending ||
                (!!value.auto_organize && !value.model)
              }
            >
              {t(save.isPending ? "Saving…" : "Save")}
            </Button>
          </div>
        )}
      </form>
      {owner.memory_id && (
        <Button
          variant="outline"
          render={<Link to={`${basePath}/memories/${owner.memory_id}`} />}
        >
          {t("Browse files and revisions")}
        </Button>
      )}
      <section className="grid gap-3" aria-label={t("Organization history")}>
        <div className="flex items-center justify-between gap-2">
          <h3>{t("Organization history")}</h3>
          <span className="text-muted-foreground text-xs">
            {t(
              status?.active_run_id
                ? "Organizing"
                : status?.pending
                  ? "Pending"
                  : "Idle",
            )}
          </span>
        </div>
        {status?.failure && (
          <p role="alert" className="text-destructive text-sm">
            {status.failure.message}
          </p>
        )}
        <ErrorNotice
          error={history.error}
          retry={() => void history.refetch()}
        />
        {history.isPending ? (
          <Loading variant="list" rows={2} />
        ) : history.data?.items.length ? (
          <ul className="grid gap-2">
            {history.data.items.map((run) => (
              <li key={run.id} className="bg-muted/40 rounded-lg p-3">
                <div className="flex items-center justify-between gap-3">
                  <Timestamp value={run.created_at} />
                  <StatePill state={run.status} />
                </div>
                {run.failure && (
                  <p className="text-destructive text-xs">
                    {run.failure.message}
                  </p>
                )}
                <Link
                  className="text-sm font-medium"
                  to={runPath(basePath, { ...run, run_id: run.id }, "debug")}
                >
                  {t("View run and usage")}
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-muted-foreground text-sm">
            {t(
              "No organization runs yet. Empty or unchanged memory needs no model call.",
            )}
          </p>
        )}
        <div className="flex justify-end gap-2">
          {cursor && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setCursor(undefined)}
            >
              {t("Latest")}
            </Button>
          )}
          {history.data?.next_cursor && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setCursor(history.data?.next_cursor ?? undefined)}
            >
              {t("Older")}
            </Button>
          )}
        </div>
      </section>
    </div>
  );
}

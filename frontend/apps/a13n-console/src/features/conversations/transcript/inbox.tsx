import {
  Button,
  ChoiceField,
  DisclosureSection,
  Menu,
  MenuItem,
  MenuPopup,
  MenuTrigger,
} from "a13n-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import {
  ArrowDownIcon,
  ArrowUpIcon,
  DotsThreeOutlineVerticalIcon,
  PlayIcon,
  TrashIcon,
} from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import {
  commandHeaders,
  data,
  ifMatch,
  rowTag,
  type Schema,
} from "../../../shared/api";
import {
  ErrorNotice,
  ErrorToast,
  Loading,
  StatePill,
  Timestamp,
} from "../../../shared/feedback";
import { Confirm } from "../../../shared/dialogs";
import { JsonView } from "../../../shared/forms";
import { conversationQueries, invalidateConversation, runPath } from "../api";
import { entryResubmission } from "../resubmit";
import { isInteractive } from "./run-actions";
import { InputContent } from "./user-message";
import styles from "./cards.module.css";

/** The inbox statuses each inbox view shows. */
const VIEWS = {
  queued: ["pending"],
  consumed: ["assigned", "consumed"],
  failed: ["failed"],
} satisfies Record<string, Schema["EntryStatus"][]>;

/** What is waiting behind the current run, in the order it will be consumed. */
export function ThreadInbox({
  thread,
  canRunNext = false,
}: {
  thread: Schema["ThreadView"];
  /** The inbox waits for someone to start its next message. */
  canRunNext?: boolean;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, can, basePath } = useWorkspace(),
    cache = useQueryClient(),
    navigate = useNavigate();
  const [state, setState] = useState<keyof typeof VIEWS>("queued");
  const query = useQuery({
    ...conversationQueries(client, workspace.id).inbox(thread.id, VIEWS[state]),
    enabled: can("read"),
  });
  const refresh = () => {
    void invalidateConversation(cache, workspace.id, {
      sessionId: thread.session_id,
      threadId: thread.id,
    });
  };
  const reorder = useMutation({
    mutationFn: (ids: string[]) =>
      client.http
        .PUT(
          "/api/v1/workspaces/{workspace_id}/threads/{thread_id}/inbox/order",
          {
            params: {
              path: { workspace_id: workspace.id, thread_id: thread.id },
            },
            // The order the reader saw belongs to this version of the Thread.
            headers: ifMatch(rowTag(thread)),
            body: { entry_ids: ids },
          },
        )
        .then(data),
    onSuccess: refresh,
  });
  // There is no "run next" operation: the first pending message is withdrawn
  // and submitted again, and a submission starts its message on a Thread
  // whose inbox waits. Withdrawing first means it can never run twice.
  const runNext = useMutation({
    mutationFn: async ({
      id,
      message,
    }: {
      id: string;
      message: NonNullable<ReturnType<typeof entryResubmission>>;
    }) => {
      const path = {
        workspace_id: workspace.id,
        thread_id: thread.id,
      };
      data(
        await client.http.DELETE(
          "/api/v1/workspaces/{workspace_id}/threads/{thread_id}/inbox/{entry_id}",
          {
            params: { path: { ...path, entry_id: id } },
            headers: ifMatch(rowTag(thread)),
          },
        ),
      );
      return data(
        await client.http.POST(
          "/api/v1/workspaces/{workspace_id}/threads/{thread_id}/inbox",
          {
            params: { path, header: commandHeaders(`resubmit:${id}`) },
            body: { kind: "message", ...message },
          },
        ),
      );
    },
    onSuccess: (receipt) => {
      void invalidateConversation(cache, workspace.id, {
        sessionId: thread.session_id,
        threadId: thread.id,
        runId: receipt.run?.id,
      });
      if (receipt.run)
        navigate(
          runPath(basePath, {
            session_id: receipt.run.session_id,
            thread_id: receipt.run.thread_id,
            run_id: receipt.run.id,
          }),
        );
    },
    onError: refresh,
  });
  if (!can("read")) return null;
  const items = query.data ?? [];
  const editable = isInteractive(thread) && can("run");
  const next = items.find((item) => item.kind === "message");
  const nextMessage = next && entryResubmission(next);
  // A read-only thread with a known-empty inbox has nothing to say.
  if (!editable && query.data && !items.length) return null;
  function move(index: number, offset: number) {
    const ids = items.map((item) => item.id);
    [ids[index], ids[index + offset]] = [ids[index + offset]!, ids[index]!];
    reorder.mutate(ids);
  }
  // The closed state says what is there, not a bare count beside a filter.
  const inboxSummary = (count: number, filter: string) => {
    const name = t(`state.${filter}`, { defaultValue: filter }).toLowerCase();
    return count
      ? t("{{count}} {{state}}", { count, state: name })
      : t("No {{state}} messages", { state: name });
  };
  return (
    <DisclosureSection
      className={styles.inbox}
      title={t("Queued messages")}
      summary={inboxSummary(items.length, state)}
    >
      <div className={styles.inboxBody}>
        <div className={styles.inboxToolbar}>
          <ChoiceField
            placeholder={t("Inbox state")}
            value={state}
            onValueChange={(value) => {
              if (
                value === "queued" ||
                value === "consumed" ||
                value === "failed"
              )
                setState(value);
            }}
            label={t("Inbox state")}
            variant="filter"
            options={[
              { value: "queued", label: t("Queued") },
              { value: "consumed", label: t("Consumed") },
              { value: "failed", label: t("Failed") },
            ]}
          />
          {editable && state === "queued" && (
            <Button
              size="sm"
              variant="outline"
              disabled={!canRunNext || !next || !nextMessage}
              loading={runNext.isPending}
              onClick={() =>
                next &&
                nextMessage &&
                runNext.mutate({ id: next.id, message: nextMessage })
              }
              type="button"
            >
              <PlayIcon size={13} />
              {t("Run next message")}
            </Button>
          )}
        </div>
        {editable && !canRunNext && state === "queued" && !!items.length && (
          <p className={styles.inboxNote}>
            {t(
              "Queued messages can run after the current run finishes and pending feedback is resolved.",
            )}
          </p>
        )}
        <ErrorNotice
          error={query.error}
          retry={() => {
            refresh();
            void query.refetch();
          }}
        />
        <ErrorToast error={reorder.error ?? runNext.error} />
        {runNext.data?.entry.status === "failed" && (
          <p className={styles.inboxNote} role="status">
            {t(
              "The next submission failed admission. Its details are retained in the Failed inbox view.",
            )}
          </p>
        )}
        {query.isPending ? (
          <Loading variant="list" rows={3} />
        ) : !items.length ? (
          <p className={styles.inboxNote}>
            {t("No messages in this inbox state.")}
          </p>
        ) : (
          items.map((item, index) => (
            <article key={item.id} className={styles.inboxItem}>
              <header>
                <StatePill state={item.status} />
                <Timestamp value={item.created_at} relative />
                <div className={styles.inboxActions}>
                  {editable && state === "queued" && (
                    <>
                      <Button
                        aria-label={t("Move message up")}
                        title={t("Move message up")}
                        variant="ghost"
                        disabled={index === 0 || reorder.isPending}
                        onClick={() => move(index, -1)}
                        size="icon-sm"
                        type="button"
                      >
                        <ArrowUpIcon size={13} />
                      </Button>
                      <Button
                        aria-label={t("Move message down")}
                        title={t("Move message down")}
                        variant="ghost"
                        disabled={
                          index === items.length - 1 || reorder.isPending
                        }
                        onClick={() => move(index, 1)}
                        size="icon-sm"
                        type="button"
                      >
                        <ArrowDownIcon size={13} />
                      </Button>
                    </>
                  )}
                  {editable && state === "queued" && (
                    <Menu>
                      <MenuTrigger
                        render={
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            type="button"
                            aria-label={t("Queued message actions")}
                            title={t("Queued message actions")}
                          />
                        }
                      >
                        <DotsThreeOutlineVerticalIcon size={13} weight="fill" />
                      </MenuTrigger>
                      <MenuPopup align="end">
                        <Confirm
                          subject={item.id}
                          onSuccess={refresh}
                          title={t("Delete queued message")}
                          description={t(
                            "Remove this message from the inbox permanently.",
                          )}
                          triggerElement={
                            <MenuItem
                              closeOnClick={false}
                              variant="destructive"
                            >
                              <TrashIcon size={14} />
                              {t("Delete")}
                            </MenuItem>
                          }
                          danger
                          action={() =>
                            client.http.DELETE(
                              "/api/v1/workspaces/{workspace_id}/threads/{thread_id}/inbox/{entry_id}",
                              {
                                params: {
                                  path: {
                                    workspace_id: workspace.id,
                                    thread_id: thread.id,
                                    entry_id: item.id,
                                  },
                                },
                                headers: ifMatch(rowTag(thread)),
                              },
                            )
                          }
                        />
                      </MenuPopup>
                    </Menu>
                  )}
                </div>
              </header>
              <InputContent input={item.payload} />
              {item.failure && <JsonView value={item.failure} />}
              {item.assigned_run_id && (
                <Link
                  className={styles.inboxLink}
                  to={runPath(basePath, {
                    session_id: thread.session_id,
                    thread_id: thread.id,
                    run_id: item.assigned_run_id,
                  })}
                >
                  {t("Open run")}
                </Link>
              )}
            </article>
          ))
        )}
      </div>
    </DisclosureSection>
  );
}

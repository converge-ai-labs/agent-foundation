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
import { commandHeaders, data, type Schema } from "../../../shared/api";
import {
  ErrorNotice,
  ErrorToast,
  Loading,
  StatePill,
  Timestamp,
} from "../../../shared/feedback";
import { Confirm } from "../../../shared/dialogs";
import { JsonView } from "../../../shared/forms";
import { useIdempotency } from "../../../shared/idempotency";
import { conversationQueries, invalidateConversation, runPath } from "../api";
import { InputContent } from "./user-message";
import styles from "./transcript.module.css";

/** What is waiting behind the current run, in the order it will be consumed. */
export function ThreadQueue({
  thread,
  canConsume,
}: {
  thread: Schema["ThreadResource"];
  canConsume: boolean;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, can, basePath } = useWorkspace(),
    cache = useQueryClient(),
    navigate = useNavigate();
  const [state, setState] = useState<Schema["QueuedSubmissionState"]>("queued");
  const consumeKey = useIdempotency();
  const query = useQuery({
    ...conversationQueries(client, workspace.id).queue(thread.id, state),
    enabled: can("queued_submission.read"),
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
        .POST("/api/v1/threads/{thread_id}/queued-submissions/reorder", {
          params: {
            path: { thread_id: thread.id },
            header: commandHeaders(workspace.id, crypto.randomUUID()),
          },
          body: {
            expected_queue_version: thread.queue_version,
            queued_submission_ids: ids,
          },
        })
        .then(data),
    onSuccess: refresh,
  });
  const consume = useMutation({
    mutationFn: () =>
      client.http
        .POST("/api/v1/threads/{thread_id}/queued-submissions/consume", {
          params: {
            path: { thread_id: thread.id },
            header: commandHeaders(
              workspace.id,
              consumeKey.forBody({
                expected_thread_version: thread.version,
                expected_queue_version: thread.queue_version,
              }),
            ),
          },
          body: {
            expected_thread_version: thread.version,
            expected_queue_version: thread.queue_version,
          },
        })
        .then(data),
    onSuccess: (receipt) => {
      void invalidateConversation(
        cache,
        workspace.id,
        {
          sessionId: thread.session_id,
          threadId: thread.id,
          runId: thread.current_run_id,
        },
        {
          sessionId: receipt.run?.session_id,
          threadId: receipt.run?.thread_id,
          runId: receipt.run?.run_id,
        },
      );
      consumeKey.reset();
      if (receipt.run) navigate(runPath(basePath, receipt.run));
    },
  });
  if (!can("queued_submission.read")) return null;
  const items = query.data?.items ?? [];
  const editable = thread.session_purpose === "debug" && thread.role === "root";
  // A read-only thread with a known-empty queue has nothing to say.
  if (!editable && query.data && !items.length) return null;
  function move(index: number, offset: number) {
    const ids = items.map((item) => item.queued_submission_id);
    [ids[index], ids[index + offset]] = [ids[index + offset]!, ids[index]!];
    reorder.mutate(ids);
  }
  return (
    <DisclosureSection
      className={styles.queue}
      title={t("Queued messages")}
      summary={`${items.length} · ${t(`state.${state}`, { defaultValue: state })}`}
    >
      <div className={styles.queueBody}>
        <div className={styles.queueToolbar}>
          <ChoiceField
            placeholder={t("Queue state")}
            value={state}
            onValueChange={(value) => {
              if (
                value === "queued" ||
                value === "consumed" ||
                value === "failed"
              )
                setState(value);
            }}
            label={t("Queue state")}
            variant="filter"
            options={[
              { value: "queued", label: t("Queued") },
              { value: "consumed", label: t("Consumed") },
              { value: "failed", label: t("Failed") },
            ]}
          />
          {editable &&
            state === "queued" &&
            can("queued_submission.consume") && (
              <Button
                size="sm"
                variant="outline"
                disabled={!canConsume || !items.length}
                loading={consume.isPending}
                onClick={() => consume.mutate()}
                type="button"
              >
                <PlayIcon size={13} />
                {t("Run next message")}
              </Button>
            )}
        </div>
        {editable && !canConsume && state === "queued" && (
          <p className={styles.queueNote}>
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
        <ErrorToast error={reorder.error ?? consume.error} />
        {consume.data?.outcome === "submission_failed" && (
          <p className={styles.queueNote} role="status">
            {t(
              "The next submission failed admission. Its details are retained in the Failed queue view.",
            )}
          </p>
        )}
        {query.isPending ? (
          <Loading variant="list" rows={3} />
        ) : !items.length ? (
          <p className={styles.queueNote}>
            {t("No messages in this queue state.")}
          </p>
        ) : (
          items.map((item, index) => (
            <article
              key={item.queued_submission_id}
              className={styles.queueItem}
            >
              <header>
                <StatePill state={item.state} />
                <Timestamp value={item.created_at} relative />
                <div className={styles.queueActions}>
                  {editable &&
                    state === "queued" &&
                    can("queued_submission.reorder") && (
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
                  {editable &&
                    state === "queued" &&
                    can("queued_submission.delete") && (
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
                          <DotsThreeOutlineVerticalIcon
                            size={13}
                            weight="fill"
                          />
                        </MenuTrigger>
                        <MenuPopup align="end">
                          <Confirm
                            subject={item.queued_submission_id}
                            onSuccess={refresh}
                            title={t("Delete queued message")}
                            description={t(
                              "Remove this message from the queue permanently.",
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
                                "/api/v1/queued-submissions/{queued_submission_id}",
                                {
                                  params: {
                                    path: {
                                      queued_submission_id:
                                        item.queued_submission_id,
                                    },
                                    query: { expected_version: item.version },
                                    header: commandHeaders(
                                      workspace.id,
                                      crypto.randomUUID(),
                                    ),
                                  },
                                },
                              )
                            }
                          />
                        </MenuPopup>
                      </Menu>
                    )}
                </div>
              </header>
              <InputContent input={item.submission.input} />
              {item.failure && <JsonView value={item.failure} />}
              {item.consumed_run_id && (
                <Link
                  className={styles.queueLink}
                  to={runPath(basePath, {
                    session_id: thread.session_id,
                    thread_id: thread.id,
                    run_id: item.consumed_run_id,
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

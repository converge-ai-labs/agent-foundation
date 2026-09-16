import { Button, ChoiceField, DisclosureSection } from "a13n-ui";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate } from "react-router";

import { ArrowDownIcon, ArrowUpIcon, PlayIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import {
  ErrorNotice,
  ErrorToast,
  Loading,
  StateBadge,
  Timestamp,
} from "../../shared/feedback";
import { Confirm, JsonView } from "../../shared/form";
import { useIdempotency } from "../../shared/idempotency";
import { conversationQueries, invalidateConversation, runPath } from "./api";
import styles from "./conversations.module.css";
import { InputContent } from "./items";

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
  function move(index: number, offset: number) {
    const ids = items.map((item) => item.queued_submission_id);
    [ids[index], ids[index + offset]] = [ids[index + offset]!, ids[index]!];
    reorder.mutate(ids);
  }
  return (
    <DisclosureSection
      className={styles.queue}
      title={t("Thread queue")}
      summary={`${items.length} ${t(state)}`}
    >
      <div className={styles.queueBody}>
        <div className={styles.inline}>
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
            hideLabel
            options={[
              { value: "queued", label: t("Queued") },
              { value: "consumed", label: t("Consumed") },
              { value: "failed", label: t("Failed") },
            ]}
          />
          {state === "queued" && can("queued_submission.consume") && (
            <Button
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
        {!canConsume && state === "queued" && (
          <p>
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
          <p role="status">
            {t(
              "The next submission failed admission. Its details are retained in the Failed queue view.",
            )}
          </p>
        )}
        {query.isPending ? (
          <Loading variant="list" rows={3} />
        ) : !items.length ? (
          <p>{t("No messages in this queue state.")}</p>
        ) : (
          items.map((item, index) => (
            <article
              key={item.queued_submission_id}
              className={styles.queueItem}
            >
              <header>
                <StateBadge state={item.state} />
                <Timestamp value={item.created_at} />
              </header>
              <InputContent input={item.submission.input} />
              {item.failure && <JsonView value={item.failure} />}
              {item.consumed_run_id && (
                <Link
                  to={runPath(basePath, {
                    session_id: thread.session_id,
                    thread_id: thread.id,
                    run_id: item.consumed_run_id,
                  })}
                >
                  {t("Open run")}
                </Link>
              )}
              <div className={styles.inline}>
                {state === "queued" && can("queued_submission.reorder") && (
                  <>
                    <Button
                      aria-label={t("Move message up")}
                      variant="outline"
                      disabled={index === 0 || reorder.isPending}
                      onClick={() => move(index, -1)}
                      size="icon-sm"
                      type="button"
                    >
                      {<ArrowUpIcon size={13} />}
                    </Button>
                    <Button
                      aria-label={t("Move message down")}
                      variant="outline"
                      disabled={index === items.length - 1 || reorder.isPending}
                      onClick={() => move(index, 1)}
                      size="icon-sm"
                      type="button"
                    >
                      {<ArrowDownIcon size={13} />}
                    </Button>
                  </>
                )}
                {state === "queued" && can("queued_submission.delete") && (
                  <Confirm
                    subject={item.queued_submission_id}
                    onSuccess={refresh}
                    title={t("Delete queued message")}
                    description={t(
                      "Remove this message from the queue permanently.",
                    )}
                    trigger={t("Delete")}
                    danger
                    action={() =>
                      client.http
                        .DELETE(
                          "/api/v1/queued-submissions/{queued_submission_id}",
                          {
                            params: {
                              path: {
                                queued_submission_id: item.queued_submission_id,
                              },
                              header: commandHeaders(
                                workspace.id,
                                crypto.randomUUID(),
                              ),
                            },
                            body: { expected_version: item.version },
                          },
                        )
                        .then(data)
                    }
                  />
                )}
              </div>
            </article>
          ))
        )}
      </div>
    </DisclosureSection>
  );
}

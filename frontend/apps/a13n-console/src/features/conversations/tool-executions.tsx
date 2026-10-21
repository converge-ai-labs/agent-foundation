import { Button, DisclosureSection, ModalFrame } from "a13n-ui";
import { ArrowClockwiseIcon, TerminalIcon } from "@phosphor-icons/react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { revalidateSession, useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data as responseData } from "../../service-client";
import type { Schema } from "../../shared/api";
import { ListRow, ListRows, ListRowsEmpty } from "../../shared/collection";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { CodeBlock, JsonView } from "../../shared/forms";
import { CopyableId } from "../../shared/identity";
import { conversationQueries } from "./api";
import styles from "./tool-executions.module.css";

type Execution = Schema["ExecutionView"];

export function ThreadToolExecutionsDialog({
  threadId,
  open,
  onOpenChange,
}: {
  threadId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <ModalFrame
      size="xl"
      title={t("Tool executions")}
      description={t("Retained tool calls across this thread's runs.")}
      open={open}
      onOpenChange={onOpenChange}
      closeLabel={t("Close")}
    >
      <ToolExecutions key={threadId} threadId={threadId} />
    </ModalFrame>
  );
}

/** Open inspectors fetch their own bounded pages; execution updates do not rely on Run versions. */
export function ToolExecutions({
  threadId,
  runId,
}: {
  threadId: string;
  runId?: string;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace();
  const query = useInfiniteQuery(
    conversationQueries(client, workspace.id).toolExecutions(threadId, runId),
  );
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <div className={styles.executions}>
      <div className={styles.toolbar}>
        <p>
          {t(
            "Calls can remain active after a run ends. Refresh to check their latest state.",
          )}
        </p>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          loading={query.isFetching}
          onClick={() => void query.refetch()}
        >
          <ArrowClockwiseIcon size={14} aria-hidden="true" />
          {t("Refresh")}
        </Button>
      </div>
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading variant="list" rows={2} />
      ) : !items.length && !query.error ? (
        <ListRowsEmpty>{t("No retained tool executions.")}</ListRowsEmpty>
      ) : (
        <ListRows>
          {items.map((execution) => (
            <ExecutionRow key={execution.execution_id} execution={execution} />
          ))}
        </ListRows>
      )}
      {query.hasNextPage && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          loading={query.isFetchingNextPage}
          onClick={() => void query.fetchNextPage()}
        >
          {t("Load more")}
        </Button>
      )}
    </div>
  );
}

function ExecutionRow({ execution }: { execution: Execution }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <div className={styles.execution}>
      <ListRow
        className={styles.header}
        icon={<TerminalIcon size={16} aria-hidden="true" />}
        name={execution.tool_name}
        secondary={
          <span className={styles.secondary}>
            <CopyableId value={execution.execution_id} />
            <Timestamp value={execution.created_at} relative />
          </span>
        }
        control={<StatePill state={execution.status ?? "unknown"} />}
        actions={
          <Button
            type="button"
            size="sm"
            variant="ghost"
            aria-expanded={open}
            aria-label={t("Inspect {{name}}", { name: execution.tool_name })}
            onClick={() => setOpen(!open)}
          >
            {t(open ? "Hide details" : "Inspect")}
          </Button>
        }
      />
      {open && (
        <div className={styles.inspector}>
          {execution.cancel_requested_at && !execution.completed_at && (
            <p role="status">
              {t("Cancellation requested. Cleanup may still be running.")}
            </p>
          )}
          {execution.status === "lost" && (
            <p role="status">
              {t(
                "The execution owner was lost. Effects may have occurred; inspect before retrying.",
              )}
            </p>
          )}
          {execution.error && (
            <p className={styles.failure}>{execution.error}</p>
          )}
          <dl className={styles.facts}>
            <div>
              <dt>{t("Run")}</dt>
              <dd>
                <CopyableId value={execution.run_id} />
              </dd>
            </div>
            <div>
              <dt>{t("Attempt")}</dt>
              <dd>
                <CopyableId value={execution.attempt_id} />
              </dd>
            </div>
            <div>
              <dt>{t("Worker")}</dt>
              <dd>
                <CopyableId value={execution.worker_id} />
              </dd>
            </div>
            {execution.completed_at && (
              <div>
                <dt>{t("Completed")}</dt>
                <dd>
                  <Timestamp value={execution.completed_at} precise />
                </dd>
              </div>
            )}
            {execution.expires_at && (
              <div>
                <dt>{t("Result expires")}</dt>
                <dd>
                  <Timestamp value={execution.expires_at} precise />
                </dd>
              </div>
            )}
          </dl>
          {execution.result_available ? (
            <ExecutionResult
              key={execution.execution_id}
              execution={execution}
            />
          ) : (
            <p>
              {t(
                execution.completed_at
                  ? "No retained result payload."
                  : "The result is not available yet.",
              )}
            </p>
          )}
          <DisclosureSection title={t("Raw metadata")}>
            <JsonView value={execution} />
          </DisclosureSection>
        </div>
      )}
    </div>
  );
}

/** A result fragment lives only in its open inspector, never in transcript or query state. */
function ExecutionResult({ execution }: { execution: Execution }) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace();
  const [fragment, setFragment] = useState<Schema["TextRead"]>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>();
  const request = useRef<AbortController | null>(null);
  const requestedOffset = useRef(0);
  useEffect(() => () => request.current?.abort(), []);
  async function read(offset = 0) {
    requestedOffset.current = offset;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setLoading(true);
    setError(undefined);
    try {
      const value = await client
        .workspace(workspace.id)
        .GET(
          "/api/v1/threads/{thread_id}/tool-executions/{execution_id}/result",
          {
            params: {
              path: {
                thread_id: execution.thread_id,
                execution_id: execution.execution_id,
              },
              query: {
                offset,
                limit: 4000,
                version: offset ? fragment?.version : undefined,
              },
            },
            signal: controller.signal,
          },
        )
        .then(responseData);
      if (!controller.signal.aborted) setFragment(value);
    } catch (failure) {
      if (!controller.signal.aborted) {
        revalidateSession(failure);
        setError(failure);
      }
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }
  return (
    <section
      className={styles.result}
      aria-label={t("Result")}
      aria-busy={loading || undefined}
    >
      {fragment && (
        <>
          <CodeBlock>{fragment.text}</CodeBlock>
          <p>
            {t("Characters {{start}}–{{end}} of {{total}}", {
              start: fragment.offset + 1,
              end: fragment.offset + Array.from(fragment.text).length,
              total: fragment.total_chars,
            })}
          </p>
          {fragment.source_truncated && (
            <p>{t("Only the retained prefix is available.")}</p>
          )}
        </>
      )}
      <div className={styles.toolbar}>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          loading={loading}
          onClick={() => void read()}
        >
          {t(fragment ? "Read from start" : "Read result")}
        </Button>
        {fragment?.next_offset != null && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={loading}
            onClick={() => void read(fragment.next_offset!)}
          >
            {t("Next fragment")}
          </Button>
        )}
      </div>
      <ErrorNotice
        error={error}
        retry={() => void read(requestedOffset.current)}
      />
    </section>
  );
}

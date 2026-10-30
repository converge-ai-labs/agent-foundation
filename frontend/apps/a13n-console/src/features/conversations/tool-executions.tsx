import { Button, DisclosureSection } from "a13n-ui";
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
  const query = useInfiniteQuery({
    ...conversationQueries(client, workspace.id).toolExecutions(
      threadId,
      runId,
    ),
    refetchInterval: 5000,
  });
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  return (
    <div className={styles.executions}>
      <div className={styles.toolbar}>
        <p>
          {t(
            "Read-only observation. Active calls refresh automatically, even after their run ends.",
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
          <ExecutionOutput execution={execution} />
          <DisclosureSection title={t("Execution details")}>
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
          </DisclosureSection>
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

/** Output stays in the open inspector, never in transcript or persisted query state. */
function ExecutionOutput({ execution }: { execution: Execution }) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace();
  const [value, setValue] = useState<Schema["ExecutionObservation"]>();
  const [error, setError] = useState<unknown>();
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function observe() {
      if (document.hidden) {
        timer = setTimeout(() => void observe(), 2000);
        return;
      }
      try {
        const result = await client
          .workspace(workspace.id)
          .GET(
            "/api/v1/threads/{thread_id}/tool-executions/{execution_id}/observation",
            {
              params: {
                path: {
                  thread_id: execution.thread_id,
                  execution_id: execution.execution_id,
                },
              },
              signal: controller.signal,
            },
          )
          .then(responseData);
        if (controller.signal.aborted) return;
        setValue(result);
        setError(undefined);
        if (!result.execution.completed_at)
          timer = setTimeout(() => void observe(), 2000);
      } catch (failure) {
        if (!controller.signal.aborted) {
          revalidateSession(failure);
          setValue(undefined);
          setError(failure);
        }
      }
    }
    void observe();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [
    client,
    workspace.id,
    execution.thread_id,
    execution.execution_id,
    execution.completed_at,
    refresh,
  ]);
  return (
    <section className={styles.result} aria-label={t("Output")}>
      <div className={styles.toolbar}>
        <strong>
          {t(value?.source === "retained" ? "Retained output" : "Live output")}
        </strong>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => setRefresh((n) => n + 1)}
        >
          {t("Refresh output")}
        </Button>
      </div>
      <ErrorNotice error={error} />
      {!value && !error && <Loading variant="list" rows={1} />}
      {value?.source === "unavailable" && (
        <p role="status">
          {t(
            "Live output is temporarily unavailable. Execution status is shown above.",
          )}
        </p>
      )}
      {value?.shell && (
        <>
          <p>
            {t("Process")}: {value.shell.status.phase} · {t("Cleanup")}:{" "}
            {value.shell.status.cleanup}
            {value.shell.status.exit_code != null && (
              <>
                {" "}
                · {t("Exit code")}: {value.shell.status.exit_code}
              </>
            )}
          </p>
          {(["stdout", "stderr"] as const).map((name) => {
            const stream = value.shell![name];
            return (
              <div className={styles.stream} key={name}>
                <div className={styles.toolbar}>
                  <span>{name}</span>
                  {stream.produced_bytes != null && (
                    <span>
                      {t("{{count}} bytes produced", {
                        count: stream.produced_bytes,
                      })}
                    </span>
                  )}
                </div>
                {stream.text ? (
                  <CodeBlock>{stream.text}</CodeBlock>
                ) : (
                  <p>{t("No output captured.")}</p>
                )}
                {stream.truncated && (
                  <p>
                    {t(
                      "Partial output preview. Some captured content is omitted.",
                    )}
                  </p>
                )}
              </div>
            );
          })}
        </>
      )}
      {value?.source === "retained" && !value.shell && (
        <p>
          {t(
            "No structured Shell output. Read the retained result below if available.",
          )}
        </p>
      )}
    </section>
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

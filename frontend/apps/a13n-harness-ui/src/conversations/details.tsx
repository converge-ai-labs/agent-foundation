import { useThreadWork, workCaption } from "./work";
import { useState } from "react";
import { useInfiniteQuery, useMutation, useQuery } from "@tanstack/react-query";
import { Button, ChoiceField, readContentParts } from "a13n-ui";
import { ApiError, result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import { ErrorNotice, TextField } from "../shell/ui";
import { ConversationConfiguration } from "./configuration";
import { MessageText } from "./message-text";
import { ChildSavedOutputs } from "./child-output";
import { AppCard } from "../mcp-apps/app-card";
import { useOperation, useThreads } from "./queries";
import type { FocusDisplay } from "./stream";
import { LiveOutput, RecoveryNotice } from "./transcript";
import { sourceText } from "./tool-presentation";
import { useChildControlState } from "./child-controls";
import styles from "./conversation.module.css";
import { useThreadUsage, useContextUsage } from "./usage";
import { UsageDetails } from "./usage-details";

export function ConversationDetails({
  threadId,
  receipt,
  reconcile,
  readOnly = false,
}: {
  threadId: string;
  receipt?: string | null;
  continuation?: string | null;
  readOnly?: boolean;
  reconcile: () => void;
}) {
  const [tab, setTab] = useState("execution");
  // Activity owns the latest process-local terminal receipt. Focus snapshots only
  // carry an active operation, so an inactive snapshot cannot substitute for it.
  const activity = useThreads(threadId, undefined, true, {
    enabled: tab === "execution" && !receipt && !readOnly,
  });
  const row = activity.data?.pages
    .flatMap((page) => page.rows)
    .find((item) => item.thread.thread_id === threadId);
  return (
    <div className={styles.form}>
      <ChoiceField
        label="Inspect"
        value={tab}
        onValueChange={setTab}
        options={[
          { value: "execution", label: "Execution & subagents" },
          { value: "configuration", label: "Configuration" },
          { value: "context", label: "Tasks, notes & usage" },
        ]}
      />
      {tab === "execution" && (
        <>
          <ErrorNotice
            error={activity.error}
            retry={() => void activity.refetch()}
          />
          <Operation
            threadId={threadId}
            receipt={receipt ?? row?.latest_operation?.receipt.receipt_id}
          />
          {!row && activity.hasNextPage && (
            <Button
              variant="ghost"
              loading={activity.isFetchingNextPage}
              onClick={() => void activity.fetchNextPage()}
            >
              Find this conversation in remaining activity results
            </Button>
          )}
          <Children threadId={threadId} reconcile={reconcile} />
        </>
      )}
      {tab === "configuration" && (
        <ConversationConfiguration
          threadId={threadId}
          reconcile={reconcile}
          readOnly={readOnly}
        />
      )}
      {tab === "context" && <ContextDetails threadId={threadId} />}
    </div>
  );
}
function Operation({
  threadId,
  receipt,
}: {
  threadId: string;
  receipt?: string | null;
}) {
  const operation = useOperation(threadId, receipt);
  if (!receipt)
    return (
      <p>
        No current-process operation receipt is available. Saved history does
        not restore execution control after a server restart.
      </p>
    );
  return (
    <section>
      <h3>Root operation</h3>
      <ErrorNotice
        error={operation.error}
        retry={() => void operation.refetch()}
      />
      {operation.data && (
        <>
          <p>
            {operation.data.status} · {receipt}
          </p>
          {operation.data.failure && (
            <p role="alert">{operation.data.failure.message}</p>
          )}
          <dl className={styles.detailGrid}>
            <div>
              <dt>Started</dt>
              <dd>{operation.data.started_at || "Preparing"}</dd>
            </div>
            <div>
              <dt>Completed</dt>
              <dd>{operation.data.completed_at || "Not completed"}</dd>
            </div>
          </dl>
          {operation.data.outcome && (
            <>
              <dl className={styles.detailGrid}>
                <div>
                  <dt>Model execution</dt>
                  <dd>{operation.data.outcome.execution.status}</dd>
                </div>
                <div>
                  <dt>Saved continuation</dt>
                  <dd>
                    {operation.data.outcome.continuation.status.replaceAll(
                      "_",
                      " ",
                    )}
                  </dd>
                </div>
                <div>
                  <dt>Environment publication</dt>
                  <dd>
                    {operation.data.outcome.environment.published} published ·{" "}
                    {operation.data.outcome.environment.failed} failed
                  </dd>
                </div>
              </dl>
              {[
                operation.data.outcome.execution.failure,
                operation.data.outcome.continuation.failure,
                ...(operation.data.outcome.environment.cleanup_failures ?? []),
              ]
                .filter((failure) => !!failure)
                .map((failure, index) => (
                  <p key={index} role="alert">
                    {failure.message}
                  </p>
                ))}
            </>
          )}
          {operation.data.outcome && (
            <details className={styles.activity}>
              <summary>
                Execution, continuation and Environment outcomes
              </summary>
              <p>
                Model execution, saved continuation and Environment cleanup are
                separate outcomes.
              </p>
              <pre className={styles.code}>
                {JSON.stringify(operation.data.outcome, null, 2)}
              </pre>
            </details>
          )}
        </>
      )}
    </section>
  );
}
export function useChildExecutions(threadId: string, enabled = true) {
  const { client } = useTransport();
  return useInfiniteQuery({
    queryKey: ["thread", threadId, "children"],
    enabled,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) =>
      result(
        client.GET("/api/threads/{thread_id}/children", {
          params: {
            path: { thread_id: threadId },
            query: { cursor: pageParam, limit: 20 },
          },
          signal,
        }),
      ),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
}
export function Children({
  threadId,
  reconcile,
  display,
}: {
  threadId: string;
  reconcile: () => void;
  display?: FocusDisplay;
}) {
  const children = useChildExecutions(threadId);
  return (
    <section className={styles.children}>
      {!display && <h3>Subagents</h3>}
      {children.isPending && <p role="status">Loading subagents…</p>}
      <ErrorNotice
        error={children.error}
        retry={() => void children.refetch()}
      />
      {children.data?.pages
        .flatMap((page) => page.executions)
        .map((child) => (
          <Child
            key={child.execution_id}
            child={child}
            reconcile={reconcile}
            live={display?.childOutput(child)}
          />
        ))}
      {children.data?.pages[0].total === 0 && <p>No subagent executions.</p>}
      {children.hasNextPage && (
        <Button
          variant="ghost"
          loading={children.isFetchingNextPage}
          onClick={() => void children.fetchNextPage()}
        >
          Load more children
        </Button>
      )}
    </section>
  );
}
export function Child({
  child,
  reconcile,
  live,
}: {
  child: Schema<"ChildExecutionView">;
  reconcile: () => void;
  live?: FocusDisplay;
}) {
  const { client } = useTransport();
  const [open, setOpen] = useState(false);
  const { state: controlState, update: updateControl } = useChildControlState(
    child.parent_thread_id,
    child.execution_id,
  );
  const { instruction, unknown, pending } = controlState;
  const setInstruction = (value: string) =>
    updateControl({ instruction: value });
  const path = {
    thread_id: child.parent_thread_id,
    execution_id: child.execution_id,
  };
  const control = useMutation({
    mutationFn: (action: "cancel" | "steer") =>
      action === "cancel"
        ? result(
            client.POST(
              "/api/threads/{thread_id}/children/{execution_id}/cancel",
              { params: { path } },
            ),
          )
        : result(
            client.POST(
              "/api/threads/{thread_id}/children/{execution_id}/steer",
              { params: { path }, body: { prompt: instruction } },
            ),
          ),
    onMutate: () =>
      updateControl({ pending: true, error: undefined, outcome: undefined }),
    onSuccess: (outcome, action) => {
      if (outcome.execution_id !== child.execution_id) {
        updateControl({ unknown: true });
        return;
      }
      updateControl({
        outcome,
        ...(outcome.accepted && action === "steer" ? { instruction: "" } : {}),
      });
    },
    onError: (error) =>
      updateControl({
        error,
        unknown: !(error instanceof ApiError) || error.status >= 500,
      }),
    onSettled: () => {
      updateControl({ pending: false });
      reconcile();
    },
  });
  return (
    <details
      className={styles.activity}
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        {child.subagent_name} · {child.persisted_status}
        {child.persisted_status === "running" &&
        child.local_status === "unavailable"
          ? " · control unavailable"
          : ""}
      </summary>
      {open && (
        <>
          <small>
            {child.execution_id} · segment {child.segment_index}
          </small>
          <dl className={styles.detailGrid}>
            <div>
              <dt>Started</dt>
              <dd>{new Date(child.created_at).toLocaleString()}</dd>
            </div>
            <div>
              <dt>Completed</dt>
              <dd>
                {child.completed_at
                  ? new Date(child.completed_at).toLocaleString()
                  : "Not completed"}
              </dd>
            </div>
          </dl>
          <ChildPresentation child={child} live={live} />
          {child.failure && <p role="alert">{child.failure.message}</p>}
          <ErrorNotice error={controlState.error} />
          {child.available_actions?.includes("steer") && (
            <div className={styles.form}>
              <TextField
                label={`Instruction for ${child.subagent_name}`}
                value={instruction}
                onChange={setInstruction}
                disabled={pending || unknown}
              />
              <Button
                variant="outline"
                disabled={!instruction.trim() || pending || unknown}
                onClick={() => control.mutate("steer")}
              >
                Guide subagent
              </Button>
            </div>
          )}
          {child.available_actions?.includes("cancel") && (
            <Button
              variant="outline"
              disabled={pending || unknown}
              onClick={() => control.mutate("cancel")}
            >
              Stop subagent
            </Button>
          )}
          {unknown && (
            <div className={styles.warning}>
              <p>
                Acknowledgement unavailable. The subagent may already have
                received this control. Your instruction is retained; no retry
                was sent.
              </p>
              <Button
                variant="outline"
                onClick={() => {
                  reconcile();
                }}
              >
                Refresh child state
              </Button>
              <Button
                variant="ghost"
                onClick={() => {
                  updateControl({
                    unknown: false,
                    error: undefined,
                    outcome: undefined,
                  });
                  control.reset();
                }}
              >
                I checked the execution; allow a new control
              </Button>
            </div>
          )}
          {controlState.outcome && !unknown && (
            <p role="status">
              {controlState.outcome.accepted
                ? "Control accepted."
                : "This execution did not accept the control."}
            </p>
          )}
        </>
      )}
    </details>
  );
}
function ChildPresentation({
  child,
  live,
}: {
  child: Schema<"ChildExecutionView">;
  live?: FocusDisplay;
}) {
  const { client } = useTransport();
  const inspection = useQuery({
    queryKey: [
      "thread",
      child.parent_thread_id,
      "child-review",
      child.execution_id,
    ],
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/threads/{thread_id}/children", {
          params: {
            path: { thread_id: child.parent_thread_id },
            query: { execution_id: child.execution_id },
          },
          signal,
        }),
      ),
  });
  const activity =
    inspection.data?.executions?.find(
      (item) => item.execution_id === child.execution_id,
    )?.activity ?? child.activity;
  const observed = live && [...live.blocks.values()];
  // Snapshot tool IDs are synthetic; do not guess a join with observed tool IDs.
  const tools = observed?.some((block) => block.kind === "tool")
    ? observed.filter((block) => block.kind === "tool")
    : [
        ...(activity?.recent_tool_calls ?? []),
        ...(activity?.active_tool_calls ?? []),
      ].map((tool) => ({
        id: tool.tool_call_id,
        kind: "tool" as const,
        name: tool.tool_name,
        text: sourceText(tool.arguments),
        result: tool.result == null ? undefined : sourceText(tool.result),
        resultParts: readContentParts(tool.content_parts),
        subagentRunId: tool.subagent_run_id ?? undefined,
        done: tool.status !== "running",
        outcome: tool.status === "running" ? undefined : tool.status,
        stopped: child.persisted_status !== "running",
      }));
  const content =
    observed?.filter(
      (block) => block.kind === "assistant" || block.kind === "thinking",
    ) ?? [];
  const current = content.length ? (
    <LiveOutput
      blocks={content}
      gap={live?.gap ?? false}
      label="Current observed output"
    />
  ) : activity?.output_preview ? (
    <section>
      <small>Activity preview · not a saved result</small>
      <MessageText text={activity?.output_preview} />
      {activity?.output_truncated && (
        <p>Some activity is outside this preview.</p>
      )}
    </section>
  ) : (
    <p>No output available yet.</p>
  );
  const running = child.persisted_status === "running";
  return (
    <div className={styles.childLive}>
      <ErrorNotice
        error={inspection.error}
        retry={() => void inspection.refetch()}
      />
      {inspection.isPending && !activity && (
        <p role="status">Loading activity…</p>
      )}
      <LiveOutput blocks={tools} gap={false} />
      {(
        inspection.data?.executions?.find(
          (item) => item.execution_id === child.execution_id,
        )?.mcp_apps ??
        child.mcp_apps ??
        []
      )
        .filter(
          (reference) =>
            !observed?.some((block) =>
              block.apps?.some((app) => app.app_id === reference.app_id),
            ),
        )
        .map((reference) => (
          <AppCard key={reference.app_id} reference={reference} />
        ))}
      {!!activity?.dropped_tool_calls && (
        <small>Earlier tools are outside this activity window.</small>
      )}
      <ChildSavedOutputs
        threadId={child.parent_thread_id}
        rootThreadId={child.root_thread_id}
        executionId={child.execution_id}
        fallback={running ? null : current}
      />
      {running && current}
      <RecoveryNotice recovery={live?.recovery} />
    </div>
  );
}
function ContextDetails({ threadId }: { threadId: string }) {
  const work = useThreadWork(threadId, { tasks: true, notes: true });
  const tasks = { ...work.tasks, data: work.tasks.data?.tasks.page };
  const notes = { ...work.notes, data: work.notes.data?.notes.page };
  const usage = useThreadUsage(threadId);
  const context = useContextUsage(threadId);
  return (
    <div className={styles.form}>
      <ErrorNotice
        error={
          work.summary.error ||
          tasks.error ||
          notes.error ||
          usage.error ||
          context.error
        }
      />
      <section>
        <h3>Tasks</h3>
        <p>{workCaption(work.tasks.data, work.stale, tasks.isFetching)}</p>
        {!tasks.data ? (
          <p>
            {tasks.isPending
              ? "Loading tasks…"
              : "Task observation unavailable."}
          </p>
        ) : tasks.data.available === false ? (
          <p>Task projection is unavailable.</p>
        ) : tasks.data?.tasks?.length ? (
          tasks.data.tasks.map((task) => (
            <div key={task.task_id} className={styles.activity}>
              <strong>
                {task.status === "in_progress"
                  ? task.active_form || task.subject
                  : task.subject}
              </strong>
              <p>
                {task.status} · {task.owner || "Unassigned"}
              </p>
              {!!task.blocked_by?.length && (
                <small>Blocked by: {task.blocked_by.join(", ")}</small>
              )}
            </div>
          ))
        ) : (
          <p>No tasks yet.</p>
        )}
        {!!tasks.data?.omitted && <p>{tasks.data.omitted} tasks omitted.</p>}
      </section>
      <section>
        <h3>Notes</h3>
        <p>{workCaption(work.notes.data, work.stale, notes.isFetching)}</p>
        {!notes.data ? (
          <p>
            {notes.isPending
              ? "Loading notes…"
              : "Note observation unavailable."}
          </p>
        ) : notes.data.notes?.length ? (
          notes.data.notes.map((note) => (
            <details key={note.key} className={styles.activity}>
              <summary>{note.key}</summary>
              <MessageText text={note.value} />
            </details>
          ))
        ) : (
          <p>No notes yet.</p>
        )}
        {!!notes.data?.omitted && <p>{notes.data.omitted} notes omitted.</p>}
      </section>
      <section>
        <h3>Context</h3>
        <p>Last reported request footprint, not a live token counter.</p>
        <dl className={styles.detailGrid}>
          <div>
            <dt>Request tokens</dt>
            <dd>
              {context.data?.latest_request_tokens?.toLocaleString() ??
                "Not reported"}
            </dd>
          </div>
          <div>
            <dt>Context window</dt>
            <dd>
              {context.data?.context_window?.toLocaleString() ?? "Not reported"}
            </dd>
          </div>
          <div>
            <dt>Model</dt>
            <dd>{context.data?.model_id ?? "Not reported"}</dd>
          </div>
          <div>
            <dt>Thinking (last captured request)</dt>
            <dd>{context.data?.thinking_summary ?? "Not reported"}</dd>
          </div>
        </dl>
      </section>
      <section>
        <h3>Observed usage</h3>
        <UsageDetails usage={usage.data} />
        {usage.data && (
          <details className={styles.activity}>
            <summary>Usage records and recent Runs</summary>
            <pre className={styles.code}>
              {JSON.stringify(usage.data, null, 2)}
            </pre>
          </details>
        )}
      </section>
    </div>
  );
}

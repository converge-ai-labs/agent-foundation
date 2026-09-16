import { useState } from "react";
import { useInfiniteQuery, useMutation, useQuery } from "@tanstack/react-query";
import { Button, ChoiceField } from "a13n-ui";
import { ApiError, result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import { ErrorNotice, TextField } from "../shell/ui";
import { ConversationConfiguration } from "./configuration";
import { MessageText } from "./message-text";
import { ChildSavedOutputs } from "./child-output";
import { useOperation, useThreads } from "./queries";
import type { FocusDisplay } from "./stream";
import { LiveOutput, RecoveryNotice } from "./transcript";
import { sourceText } from "./tool-presentation";
import { useChildControlState } from "./child-controls";
import styles from "./conversation.module.css";
import { useThreadUsage, useContextUsage } from "./usage";

export function ConversationDetails({
  threadId,
  receipt,
  continuation,
  reconcile,
}: {
  threadId: string;
  receipt?: string | null;
  continuation?: string | null;
  reconcile: () => void;
}) {
  const [tab, setTab] = useState("execution");
  // Activity owns the latest process-local terminal receipt. Focus snapshots only
  // carry an active operation, so an inactive snapshot cannot substitute for it.
  const activity = useThreads(threadId, undefined, true, {
    enabled: tab === "execution" && !receipt,
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
          { value: "execution", label: "Execution & children" },
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
        <ConversationConfiguration threadId={threadId} reconcile={reconcile} />
      )}
      {tab === "context" && (
        <ContextDetails threadId={threadId} continuation={continuation} />
      )}
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
export function useChildExecutions(threadId: string) {
  const { client } = useTransport();
  return useInfiniteQuery({
    queryKey: ["thread", threadId, "children"],
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
      {!display && <h3>Child executions</h3>}
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
      {children.data?.pages[0].total === 0 && <p>No child executions.</p>}
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
                Send child instruction
              </Button>
            </div>
          )}
          {child.available_actions?.includes("cancel") && (
            <Button
              variant="outline"
              disabled={pending || unknown}
              onClick={() => control.mutate("cancel")}
            >
              Stop child
            </Button>
          )}
          {unknown && (
            <div className={styles.warning}>
              <p>
                Acknowledgement unavailable. The child may already have received
                this control. Your instruction is retained; no retry was sent.
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
  const observed = live && [...live.blocks.values()];
  // Snapshot tool IDs are synthetic; do not guess a join with observed tool IDs.
  const tools = observed?.some((block) => block.kind === "tool")
    ? observed.filter((block) => block.kind === "tool")
    : [
        ...(child.activity.recent_tool_calls ?? []),
        ...(child.activity.active_tool_calls ?? []),
      ].map((tool) => ({
        id: tool.tool_call_id,
        kind: "tool" as const,
        name: tool.tool_name,
        text: sourceText(tool.arguments),
        result: tool.result == null ? undefined : sourceText(tool.result),
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
  ) : child.activity.output_preview ? (
    <section>
      <small>Activity preview · not a saved result</small>
      <MessageText text={child.activity.output_preview} />
      {child.activity.output_truncated && (
        <p>Some activity is outside this preview.</p>
      )}
    </section>
  ) : (
    <p>No output available yet.</p>
  );
  const running = child.persisted_status === "running";
  return (
    <div className={styles.childLive}>
      <LiveOutput blocks={tools} gap={false} />
      {!!child.activity.dropped_tool_calls && (
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
function ContextDetails({
  threadId,
  continuation,
}: {
  threadId: string;
  continuation?: string | null;
}) {
  const { client } = useTransport();
  const params = {
    path: { thread_id: threadId },
    query: { expected_continuation_id: continuation ?? undefined },
  };
  const tasks = useQuery({
    queryKey: ["thread", threadId, "tasks", continuation],
    queryFn: ({ signal }) =>
      result(client.GET("/api/threads/{thread_id}/tasks", { params, signal })),
  });
  const notes = useQuery({
    queryKey: ["thread", threadId, "notes", continuation],
    queryFn: ({ signal }) =>
      result(client.GET("/api/threads/{thread_id}/notes", { params, signal })),
  });
  const usage = useThreadUsage(threadId);
  const context = useContextUsage(threadId);
  return (
    <div className={styles.form}>
      <ErrorNotice
        error={tasks.error || notes.error || usage.error || context.error}
      />
      <section>
        <h3>Tasks</h3>
        {tasks.data?.available === false ? (
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
          <p>No saved tasks.</p>
        )}
        {!!tasks.data?.omitted && <p>{tasks.data.omitted} tasks omitted.</p>}
      </section>
      <section>
        <h3>Notes</h3>
        {notes.data?.notes?.length ? (
          notes.data.notes.map((note) => (
            <details key={note.key} className={styles.activity}>
              <summary>{note.key}</summary>
              <MessageText text={note.value} />
            </details>
          ))
        ) : (
          <p>No saved notes.</p>
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
        <p>
          Root and descendants are reported separately. Unknown costs are not
          zero-cost usage.
        </p>
        {usage.data && (
          <>
            <dl className={styles.detailGrid}>
              <div>
                <dt>Root model cost</dt>
                <dd>${usage.data.root.model_cost_usd}</dd>
              </div>
              <div>
                <dt>Descendant model cost</dt>
                <dd>${usage.data.descendants.model_cost_usd}</dd>
              </div>
              <div>
                <dt>Model requests</dt>
                <dd>{usage.data.combined.model_requests}</dd>
              </div>
              <div>
                <dt>Unknown model / provider costs</dt>
                <dd>
                  {usage.data.combined.unknown_model_costs} /{" "}
                  {usage.data.combined.unknown_provider_costs}
                </dd>
              </div>
            </dl>
            <dl className={styles.detailGrid}>
              {usage.data.combined.tokens.map(([name, count]) => (
                <div key={name}>
                  <dt>{name}</dt>
                  <dd>{count.toLocaleString()}</dd>
                </div>
              ))}
            </dl>
            <details className={styles.activity}>
              <summary>Models, providers and recent Runs</summary>
              <pre className={styles.code}>
                {JSON.stringify(usage.data, null, 2)}
              </pre>
            </details>
          </>
        )}
      </section>
    </div>
  );
}

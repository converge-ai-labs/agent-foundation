import activityStyles from "./work-inspector.module.css";
import { useThreadWork, workCaption } from "./work";
import { TaskList, NoteList } from "./work-lists";
import { useRef, useState, type ReactNode } from "react";
import { useInfiniteQuery, useMutation, useQuery } from "@tanstack/react-query";
import {
  Button,
  Tabs,
  TabsList,
  TabsTab,
  TabsPanel,
  readContentParts,
} from "a13n-ui";
import { ApiError, result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import { ErrorNotice, TextField } from "../shell/ui";
import { ConversationConfiguration } from "./configuration";
import { MessageText } from "./message-text";
import { ChildSavedOutputs } from "./child-output";
import { ToolExecutions } from "./tool-executions";
import { AppCard } from "../mcp-apps/app-card";
import { useOperation, useThreads } from "./queries";
import type { FocusDisplay } from "./stream";
import { LiveOutput, RecoveryNotice } from "./transcript";
import { sourceText } from "./tool-presentation";
import { useChildControlState } from "./child-controls";
import layout from "../shell/workbench.module.css";
import styles from "./conversation.module.css";
import { useThreadUsage, useContextUsage } from "./usage";
import { DebugData, DebugId } from "../shell/debug";
import {
  UsageDetails,
  ContextDetails as ContextUsageDetails,
} from "./usage-details";
import { SettingsRow, SettingsSection } from "../shell/settings";

export function ConversationDetails({
  threadId,
  receipt,
  reconcile,
  readOnly = false,
  management,
  actionsContainer,
}: {
  threadId: string;
  receipt?: string | null;
  continuation?: string | null;
  readOnly?: boolean;
  management?: ReactNode;
  actionsContainer?: HTMLElement | null;
  reconcile: () => void;
}) {
  const root = useRef<HTMLDivElement>(null);
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
    <Tabs
      ref={root}
      className={styles.inspection}
      value={tab}
      onValueChange={(value) => {
        setTab(String(value));
        root.current
          ?.closest("[data-slot=scroll-area-viewport]")
          ?.scrollTo({ top: 0 });
      }}
    >
      <TabsList variant="underline" aria-label="Chat details">
        <TabsTab value="execution">Activity</TabsTab>
        <TabsTab value="configuration">Configuration</TabsTab>
        <TabsTab value="context">Tasks & usage</TabsTab>
      </TabsList>
      <TabsPanel value="execution">
        <>
          <ErrorNotice
            error={activity.error}
            retry={() => void activity.refetch()}
          />
          {!receipt && !readOnly && activity.isPending ? (
            <p role="status">Loading activity…</p>
          ) : !receipt && !row && activity.hasNextPage ? null : (
            <Operation
              threadId={threadId}
              receipt={receipt ?? row?.latest_operation?.receipt.receipt_id}
            />
          )}
          {!row && activity.hasNextPage && (
            <Button
              variant="ghost"
              loading={activity.isFetchingNextPage}
              onClick={() => void activity.fetchNextPage()}
            >
              Load earlier
            </Button>
          )}
          <Children threadId={threadId} reconcile={reconcile} />
          {!readOnly && management && (
            <details className={layout.details}>
              <summary>Chat management</summary>
              {management}
            </details>
          )}
        </>
      </TabsPanel>
      <TabsPanel value="configuration">
        <ConversationConfiguration
          actionsContainer={actionsContainer}
          threadId={threadId}
          reconcile={reconcile}
          readOnly={readOnly}
        />
      </TabsPanel>
      <TabsPanel value="context">
        <ContextDetails threadId={threadId} />
      </TabsPanel>
    </Tabs>
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
        Run controls are unavailable after a server restart. History is kept.
      </p>
    );
  const data = operation.data;
  return (
    <section>
      <ErrorNotice
        error={operation.error}
        retry={() => void operation.refetch()}
      />
      {operation.isPending && <p role="status">Loading run…</p>}
      {data && (
        <>
          <SettingsSection title="Latest run">
            <SettingsRow label="Status">
              <span>
                {data.status.charAt(0).toUpperCase() + data.status.slice(1)}
              </span>
            </SettingsRow>
            <SettingsRow label="Started">
              <span>
                {data.started_at
                  ? new Date(data.started_at).toLocaleString()
                  : "Preparing"}
              </span>
            </SettingsRow>
            <SettingsRow label="Completed">
              <span>
                {data.completed_at
                  ? new Date(data.completed_at).toLocaleString()
                  : "Not completed"}
              </span>
            </SettingsRow>
          </SettingsSection>
          <DebugId label="Operation receipt" value={receipt} />
          {data.failure && <p role="alert">{data.failure.message}</p>}
          {data.outcome && (
            <>
              <details className={layout.details}>
                <summary>Run outcome</summary>
                <SettingsSection>
                  <SettingsRow label="Model result">
                    <span>
                      {data.outcome.execution.status.replaceAll("_", " ")}
                    </span>
                  </SettingsRow>
                  <SettingsRow label="Saved context">
                    <span>
                      {data.outcome.continuation.status.replaceAll("_", " ")}
                    </span>
                  </SettingsRow>
                  <SettingsRow label="Environment changes">
                    <span>
                      {data.outcome.environment.published} published ·{" "}
                      {data.outcome.environment.failed} failed
                    </span>
                  </SettingsRow>
                </SettingsSection>
              </details>
              {[
                data.outcome.execution.failure,
                data.outcome.continuation.failure,
                ...(data.outcome.environment.cleanup_failures ?? []),
              ]
                .filter((failure) => !!failure)
                .map((failure, index) => (
                  <p key={index} role="alert">
                    {failure.message}
                  </p>
                ))}
              <DebugData
                label="Execution, continuation and Environment outcomes"
                value={data.outcome}
              />
            </>
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
    <section
      className={`${styles.children} ${display ? activityStyles.children : ""}`}
    >
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
      {children.data?.pages[0].total === 0 && (
        <p className={activityStyles.emptyState}>No subagent executions.</p>
      )}
      {children.hasNextPage && (
        <Button
          variant="ghost"
          loading={children.isFetchingNextPage}
          onClick={() => void children.fetchNextPage()}
        >
          Load more
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
  const { client, shareId } = useTransport();
  const [open, setOpen] = useState(false);
  const [showTools, setShowTools] = useState(false);
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
          <DebugId label="Execution" value={child.execution_id} />
          <DebugData label="Execution segment" value={child.segment_index} />
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
          {!shareId && (
            <details
              open={showTools}
              onToggle={(event) => setShowTools(event.currentTarget.open)}
            >
              <summary>Tool calls</summary>
              {showTools && (
                <ToolExecutions
                  threadId={child.child_thread_id}
                  rootThreadId={child.parent_thread_id}
                />
              )}
            </details>
          )}
          {child.failure && <p role="alert">{child.failure.message}</p>}
          <ErrorNotice error={controlState.error} />
          {!shareId &&
            (child.available_actions?.includes("steer") ||
              child.available_actions?.includes("cancel")) && (
              <div className={styles.childControls}>
                {child.available_actions?.includes("steer") && (
                  <TextField
                    label={`Instruction for ${child.subagent_name}`}
                    value={instruction}
                    onChange={setInstruction}
                    disabled={pending || unknown}
                  />
                )}
                <div className={styles.childControlActions}>
                  {child.available_actions?.includes("steer") && (
                    <Button
                      variant="outline"
                      disabled={!instruction.trim() || pending || unknown}
                      onClick={() => control.mutate("steer")}
                    >
                      Guide subagent
                    </Button>
                  )}
                  {child.available_actions?.includes("cancel") && (
                    <Button
                      variant="ghost"
                      disabled={pending || unknown}
                      onClick={() => control.mutate("cancel")}
                    >
                      Stop subagent
                    </Button>
                  )}
                </div>
              </div>
            )}
          {!shareId && unknown && (
            <div className={styles.warning}>
              <p>
                Couldn't confirm delivery. The subagent may already have
                received your instruction. It is still saved here; nothing was
                resent.
              </p>
              <div className={styles.childControlActions}>
                <Button
                  variant="outline"
                  onClick={() => {
                    reconcile();
                  }}
                >
                  Refresh status
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
                  Allow new action
                </Button>
              </div>
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
        <h3>
          <span>
            Tasks{" "}
            <span className={styles.settingsHint}>
              ·{" "}
              {work.stale
                ? "Stale"
                : tasks.isPending
                  ? "Loading…"
                  : !tasks.data || tasks.data.available === false
                    ? "Unavailable"
                    : `${tasks.data.tasks?.length ?? 0}`}
            </span>
          </span>
        </h3>
        <p className={styles.settingsHint}>
          {workCaption(work.tasks.data, work.stale, tasks.isFetching)}
        </p>
        {!tasks.data ? (
          <p>
            {tasks.isPending
              ? "Loading tasks…"
              : "Task observation unavailable."}
          </p>
        ) : (
          <TaskList page={tasks.data} />
        )}
      </section>
      <section>
        <h3>
          <span>
            Notes{" "}
            <span className={styles.settingsHint}>
              ·{" "}
              {work.stale
                ? "Stale"
                : notes.isPending
                  ? "Loading…"
                  : !notes.data
                    ? "Unavailable"
                    : `${notes.data.notes?.length ?? 0}`}
            </span>
          </span>
        </h3>
        <p className={styles.settingsHint}>
          {workCaption(work.notes.data, work.stale, notes.isFetching)}
        </p>
        {!notes.data ? (
          <p>
            {notes.isPending
              ? "Loading notes…"
              : "Note observation unavailable."}
          </p>
        ) : (
          <NoteList page={notes.data} />
        )}
      </section>
      <section>
        <h3>Usage</h3>
        {usage.isPending ? (
          <p role="status">Loading usage…</p>
        ) : (
          <UsageDetails usage={usage.data} />
        )}
        <DebugData label="Usage and recent runs" value={usage.data ?? null} />
      </section>
      <details className={layout.details}>
        <summary>Context</summary>
        <ContextUsageDetails
          used={context.data?.latest_request_tokens}
          capacity={context.data?.context_window}
        />
        <DebugId label="Chat ID" value={threadId} />
        {(context.data?.model_id || context.data?.thinking_summary) && (
          <SettingsSection>
            {context.data.model_id && (
              <SettingsRow label="Model">
                <span>{context.data.model_id}</span>
              </SettingsRow>
            )}
            {context.data.thinking_summary && (
              <SettingsRow label="Thinking">
                <span>{context.data.thinking_summary}</span>
              </SettingsRow>
            )}
          </SettingsSection>
        )}
        <DebugData label="Context usage data" value={context.data ?? null} />
      </details>
    </div>
  );
}

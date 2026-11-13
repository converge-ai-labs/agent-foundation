import { useTranslation } from "../i18n";
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
import { ErrorNotice } from "../shell/ui";
import { ConversationConfiguration } from "./configuration";
import { MessageText } from "./message-text";
import { ChildSavedOutputs } from "./child-output";
import { ToolExecutions } from "./tool-executions";
import { AppCard } from "../mcp-apps/app-card";
import { useOperation, useThreads } from "./queries";
import type { FocusDisplay } from "./stream";
import { LiveOutput, RecoveryNotice } from "./transcript";
import { sourceText } from "./tool-presentation";
import { useChildControl } from "./child-controls";
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
  const { t } = useTranslation();
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
      <TabsList variant="underline" aria-label={t("Chat details")}>
        <TabsTab value="execution">{t("Activity")}</TabsTab>
        <TabsTab value="configuration">{t("Configuration")}</TabsTab>
        <TabsTab value="context">{t("Tasks & usage")}</TabsTab>
      </TabsList>
      <TabsPanel value="execution">
        <>
          <ErrorNotice
            error={activity.error}
            retry={() => void activity.refetch()}
          />
          {!receipt && !readOnly && activity.isPending ? (
            <p role="status">{t("Loading activity…")}</p>
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
              {t("Load earlier")}
            </Button>
          )}
          <Children threadId={threadId} reconcile={reconcile} />
          {!readOnly && management && (
            <details className={layout.details}>
              <summary>{t("Chat management")}</summary>
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
  const { t } = useTranslation();
  const statuses = {
    preparing: t("Preparing"),
    running: t("Running"),
    completed: t("Completed"),
    suspended: t("Suspended"),
    failed: t("Failed"),
    cancelled: t("Cancelled"),
  } satisfies Record<Schema<"RootOperationStatus">, string>;
  const executionStatuses = {
    completed: t("completed"),
    failed: t("failed"),
    cancelled: t("cancelled"),
    suspended: t("suspended"),
  } satisfies Record<Schema<"RootExecutionView">["status"], string>;
  const continuationStatuses = {
    selected: t("selected"),
    not_available: t("not available"),
    failed: t("failed"),
  } satisfies Record<Schema<"ContinuationSelectionView">["status"], string>;
  const operation = useOperation(threadId, receipt);
  if (!receipt)
    return (
      <p>
        {t(
          "Run controls are unavailable after a server restart. History is kept.",
        )}
      </p>
    );
  const data = operation.data;
  return (
    <section>
      <ErrorNotice
        error={operation.error}
        retry={() => void operation.refetch()}
      />
      {operation.isPending && <p role="status">{t("Loading run…")}</p>}
      {data && (
        <>
          <SettingsSection title={t("Latest run")}>
            <SettingsRow label={t("Status")}>
              <span>{statuses[data.status]}</span>
            </SettingsRow>
            <SettingsRow label={t("Started")}>
              <span>
                {data.started_at
                  ? new Date(data.started_at).toLocaleString()
                  : t("Preparing")}
              </span>
            </SettingsRow>
            <SettingsRow label={t("Completed")}>
              <span>
                {data.completed_at
                  ? new Date(data.completed_at).toLocaleString()
                  : t("Not completed")}
              </span>
            </SettingsRow>
          </SettingsSection>
          <DebugId label={t("Operation receipt")} value={receipt} />
          {data.failure && <p role="alert">{data.failure.message}</p>}
          {data.outcome && (
            <>
              <details className={layout.details}>
                <summary>{t("Run outcome")}</summary>
                <SettingsSection>
                  <SettingsRow label={t("Model result")}>
                    <span>
                      {executionStatuses[data.outcome.execution.status]}
                    </span>
                  </SettingsRow>
                  <SettingsRow label={t("Saved context")}>
                    <span>
                      {continuationStatuses[data.outcome.continuation.status]}
                    </span>
                  </SettingsRow>
                  <SettingsRow label={t("Environment changes")}>
                    <span>
                      {t("{{published}} published - {{failed}} failed", {
                        published: data.outcome.environment.published,
                        failed: data.outcome.environment.failed,
                      })}
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
                label={t("Execution, continuation and Environment outcomes")}
                value={data.outcome}
              />
            </>
          )}
        </>
      )}
    </section>
  );
}

export function useChildExecutions(
  threadId: string,
  { enabled = true, latest = false } = {},
) {
  const { client } = useTransport();
  return useInfiniteQuery({
    queryKey: latest
      ? ["thread", threadId, "children", "latest"]
      : ["thread", threadId, "children"],
    enabled,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) =>
      result(
        client.GET("/api/threads/{thread_id}/children", {
          params: {
            path: { thread_id: threadId },
            query: { cursor: pageParam, limit: 100, latest },
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
  const { t } = useTranslation();
  const children = useChildExecutions(threadId);
  return (
    <section
      className={`${styles.children} ${display ? activityStyles.children : ""}`}
    >
      {!display && <h3>{t("Subagents")}</h3>}
      {children.isPending && <p role="status">{t("Loading subagents…")}</p>}
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
        <p className={activityStyles.emptyState}>
          {t("No subagent executions.")}
        </p>
      )}
      {children.hasNextPage && (
        <Button
          variant="ghost"
          loading={children.isFetchingNextPage}
          onClick={() => void children.fetchNextPage()}
        >
          {t("Load more")}
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
  const { t } = useTranslation();
  const childStatuses = {
    running: t("running"),
    succeeded: t("succeeded"),
    failed: t("failed"),
    cancelled: t("cancelled"),
    lost: t("lost"),
  } satisfies Record<Schema<"ChildExecutionView">["persisted_status"], string>;
  const { client, shareId } = useTransport();
  const [open, setOpen] = useState(false);
  const [showTools, setShowTools] = useState(false);
  const { controlState, updateControl, control } = useChildControl(
    child,
    reconcile,
  );
  const { unknown, pending } = controlState;
  return (
    <details
      className={styles.activity}
      open={open}
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>
        {child.subagent_name} - {childStatuses[child.persisted_status]}
        {child.persisted_status === "running" &&
        child.local_status === "unavailable"
          ? ` - ${t("control unavailable")}`
          : ""}
      </summary>
      {open && (
        <>
          <DebugId label={t("Execution")} value={child.execution_id} />
          <DebugData
            label={t("Execution segment")}
            value={child.segment_index}
          />
          <dl className={styles.detailGrid}>
            <div>
              <dt>{t("Started")}</dt>
              <dd>{new Date(child.created_at).toLocaleString()}</dd>
            </div>
            <div>
              <dt>{t("Completed")}</dt>
              <dd>
                {child.completed_at
                  ? new Date(child.completed_at).toLocaleString()
                  : t("Not completed")}
              </dd>
            </div>
          </dl>
          <ChildPresentation child={child} live={live} />
          {!shareId && (
            <details
              open={showTools}
              onToggle={(event) => setShowTools(event.currentTarget.open)}
            >
              <summary>{t("Tool calls")}</summary>
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
          {!shareId && child.available_actions?.includes("cancel") && (
            <Button
              variant="ghost"
              disabled={pending || unknown || !!controlState.outcome?.accepted}
              onClick={() => control.mutate()}
            >
              {pending || controlState.outcome?.accepted
                ? t("Stopping…")
                : t("Stop subagent")}
            </Button>
          )}
          {!shareId && unknown && (
            <div className={styles.warning}>
              <p>{t("Couldn’t confirm delivery. Nothing was resent.")}</p>
              <div className={styles.childControlActions}>
                <Button
                  variant="outline"
                  onClick={() => {
                    reconcile();
                  }}
                >
                  {t("Refresh status")}
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
                  {t("Allow new action")}
                </Button>
              </div>
            </div>
          )}
          {controlState.outcome && !unknown && (
            <p role="status">
              {controlState.outcome.accepted
                ? t("Control accepted.")
                : t("This execution did not accept the control.")}
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
  const { t } = useTranslation();
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
      running={
        child.persisted_status === "running" && child.local_status === "active"
      }
      gap={live?.gap ?? false}
      label={t("Current observed output")}
    />
  ) : activity?.output_preview ? (
    <section>
      <small>{t("Activity preview - not a saved result")}</small>
      <MessageText text={activity?.output_preview} />
      {activity?.output_truncated && (
        <p>{t("Some activity is outside this preview.")}</p>
      )}
    </section>
  ) : (
    <p>{t("No output available yet.")}</p>
  );
  const running = child.persisted_status === "running";
  return (
    <div className={styles.childLive}>
      <ErrorNotice
        error={inspection.error}
        retry={() => void inspection.refetch()}
      />
      {inspection.isPending && !activity && (
        <p role="status">{t("Loading activity…")}</p>
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
        <small>{t("Earlier tools are outside this activity window.")}</small>
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
  const { t } = useTranslation();
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
            {t("Tasks")}{" "}
            <span className={styles.settingsHint}>
              -{" "}
              {work.stale
                ? t("Stale")
                : tasks.isPending
                  ? t("Loading…")
                  : !tasks.data || tasks.data.available === false
                    ? t("Unavailable")
                    : `${tasks.data.tasks?.length ?? 0}`}
            </span>
          </span>
        </h3>
        <p className={styles.settingsHint}>
          {workCaption(t, work.tasks.data, work.stale, tasks.isFetching)}
        </p>
        {!tasks.data ? (
          <p>
            {tasks.isPending
              ? t("Loading tasks…")
              : t("Task observation unavailable.")}
          </p>
        ) : (
          <TaskList page={tasks.data} />
        )}
      </section>
      <section>
        <h3>
          <span>
            {t("Notes")}{" "}
            <span className={styles.settingsHint}>
              -{" "}
              {work.stale
                ? t("Stale")
                : notes.isPending
                  ? t("Loading…")
                  : !notes.data
                    ? t("Unavailable")
                    : `${notes.data.notes?.length ?? 0}`}
            </span>
          </span>
        </h3>
        <p className={styles.settingsHint}>
          {workCaption(t, work.notes.data, work.stale, notes.isFetching)}
        </p>
        {!notes.data ? (
          <p>
            {notes.isPending
              ? t("Loading notes…")
              : t("Note observation unavailable.")}
          </p>
        ) : (
          <NoteList page={notes.data} />
        )}
      </section>
      <section>
        <h3>{t("Usage")}</h3>
        {usage.isPending ? (
          <p role="status">{t("Loading usage…")}</p>
        ) : (
          <UsageDetails usage={usage.data} />
        )}
        <DebugData
          label={t("Usage and recent runs")}
          value={usage.data ?? null}
        />
      </section>
      <details className={layout.details}>
        <summary>{t("Context")}</summary>
        <ContextUsageDetails
          request={context.data}
          used={context.data?.latest_request_tokens}
          capacity={context.data?.context_window}
        />
        <DebugId label={t("Chat ID")} value={threadId} />
        {(context.data?.model_id || context.data?.thinking_summary) && (
          <SettingsSection>
            {context.data.model_id && (
              <SettingsRow label={t("Model")}>
                <span>{context.data.model_id}</span>
              </SettingsRow>
            )}
            {context.data.thinking_summary && (
              <SettingsRow label={t("Thinking")}>
                <span>{context.data.thinking_summary}</span>
              </SettingsRow>
            )}
          </SettingsSection>
        )}
        <DebugData
          label={t("Context usage data")}
          value={context.data ?? null}
        />
      </details>
    </div>
  );
}

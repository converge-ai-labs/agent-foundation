import {
  Button,
  Menu,
  MenuItem,
  MenuPopup,
  MenuTrigger,
  SegmentedControl,
} from "a13n-ui";
import {
  ArrowSquareOutIcon,
  ArrowsInLineVerticalIcon,
  ArrowsOutLineVerticalIcon,
  CaretLeftIcon,
  CaretRightIcon,
  CopyIcon,
  DotsThreeOutlineVerticalIcon,
  MonitorIcon,
  PencilSimpleIcon,
  TerminalIcon,
  SlidersHorizontalIcon,
} from "@phosphor-icons/react";
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { StatePill } from "../../shared/feedback";
import { AgentAvatar } from "../agents/avatar";
import { useAgent } from "../agents/queries";
import { isActiveRun, isConsoleSession } from "./api";
import { useRun, useSession } from "./queries";
import { useViewLevel } from "./transcript/debug/view";
import { useRunCollapseAll } from "./transcript/debug/collapse";
import { useThreadRuns } from "./transcript/thread-runs";
import { SessionUsage } from "./session-usage";
import { ThreadBranches } from "./thread-branches";
import { ThreadExecutionDialog } from "./thread-execution";
import { ThreadMountsDialog } from "./thread-mounts";
import { SessionEditor } from "./session-editor";
import { sessionTitle } from "./session-identity";
import { ThreadToolExecutionsDialog } from "./tool-executions";
import styles from "./conversations.module.css";

/**
 * Who answered, which session this is, and the one switch that changes how
 * much of it you see. Everything else about a run lives in the run itself.
 * The actions menu closes the first line, so a narrow screen keeps it beside
 * the name and moves only the view controls to a second line.
 */
export function SessionHeader({
  threads,
}: {
  threads: readonly Schema["ThreadView"][];
}) {
  const { t } = useTranslation();
  const { sessionId = "", threadId = "", runId } = useParams();
  const { basePath, can } = useWorkspace();
  const [search, setSearch] = useSearchParams();
  const following = search.get("follow") === "latest";
  const thread = threads.find((entry) => entry.id === threadId);
  const root = threads.find(
    (entry) => entry.origin === "new" && entry.session_id === sessionId,
  );
  const session = useSession(sessionId);
  const { level, setLevel: switchLevel } = useViewLevel(thread, session.data);
  const run = useRun(runId);
  const valid = run.data?.session_id === sessionId;
  const agent = useAgent(valid ? run.data?.agent_id : undefined);
  const { total } = useThreadRuns(threadId);
  const collapse = useRunCollapseAll();
  const [mounts, setMounts] = useState(false);
  const [editing, setEditing] = useState(false);
  const title = session.data && sessionTitle(session.data);
  const [tools, setTools] = useState(false);
  const [execution, setExecution] = useState(false);
  const debugSession = isConsoleSession(session.data);
  // Delegated children retain their execution breadcrumb beside thread switching.
  const child = thread?.origin === "child";
  // How this session started reads the same at both levels.
  const note = debugSession
    ? [t("Debug session"), total ? t("{{count}} runs", { count: total }) : null]
        .filter(Boolean)
        .join(" · ")
    : run.data?.schedule_origin
      ? t("Scheduled execution")
      : run.data
        ? t("Started by {{trigger}}", {
            trigger: t(`trigger.${run.data.trigger}`, {
              defaultValue: run.data.trigger,
            }),
          })
        : "";
  const active =
    valid &&
    !!run.data &&
    (isActiveRun(run.data.status) || run.data.status === "waiting");
  return (
    <header className={styles.sessionHeader}>
      <Link
        className={styles.back}
        to={
          session.data?.schedule_id
            ? `${basePath}/schedules/${session.data.schedule_id}`
            : `${basePath}/sessions`
        }
      >
        <CaretLeftIcon size={13} aria-hidden="true" />
        {t(session.data?.schedule_id ? "Schedule" : "Sessions")}
      </Link>
      <span className={styles.headerRule} aria-hidden="true" />
      <div className={styles.sessionIdentity}>
        {title ? (
          <span className={styles.sessionTitle} title={title}>
            {title}
          </span>
        ) : agent.data ? (
          <Link
            className={styles.agentLink}
            to={`${basePath}/agents/${agent.data.id}`}
          >
            <AgentAvatar
              name={agent.data.name}
              id={run.data?.agent_id}
              url={agent.data.image_url}
              className={styles.headerAvatar}
            />
            <span className={styles.agentName}>{agent.data.name}</span>
          </Link>
        ) : (
          <span className={styles.agentLink}>{t("Session")}</span>
        )}
        {note && (
          <span className={styles.subNote} title={note}>
            {note}
          </span>
        )}
      </div>
      <div className={styles.sessionControls}>
        {active && run.data && (
          <span className={styles.headerState}>
            <StatePill state={run.data.status} />
          </span>
        )}
        {child && root && (
          <span className={styles.breadcrumb}>
            <Link
              to={`${basePath}/sessions/${sessionId}/threads/${root.id}?view=debug`}
            >
              {t("Root thread")}
            </Link>
            <CaretRightIcon size={11} aria-hidden="true" />
            <span>{agent.data?.name ?? t("Child thread")}</span>
          </span>
        )}
        {level === "debug" && (
          <SessionUsage
            sessionId={sessionId}
            threads={threads}
            thread={thread}
            runId={runId}
          />
        )}
        {thread && (
          <ThreadBranches thread={thread} threads={threads} level={level} />
        )}
        {thread && runId && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            aria-pressed={following}
            title={t(following ? "Pause following" : "Follow latest")}
            onClick={() =>
              setSearch(
                (params) => {
                  if (following) params.delete("follow");
                  else params.set("follow", "latest");
                  return params;
                },
                { replace: true },
              )
            }
          >
            {t(following ? "Following latest" : "Follow latest")}
          </Button>
        )}
        {thread && can("read") && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setTools(true)}
          >
            <TerminalIcon size={14} aria-hidden="true" />
            {t("Tool executions")}
          </Button>
        )}
        <SegmentedControl
          className={styles.levelSwitch}
          label={t("Disclosure level")}
          value={level}
          onValueChange={(next) =>
            switchLevel(next === "debug" ? "debug" : "chat")
          }
          options={[
            { value: "chat", label: t("Chat") },
            { value: "debug", label: t("Debug") },
          ]}
        />
      </div>
      <Menu>
        <MenuTrigger
          render={
            <Button
              variant="ghost"
              size="icon-sm"
              type="button"
              aria-label={t("Session actions")}
              title={t("Session actions")}
            />
          }
        >
          <DotsThreeOutlineVerticalIcon size={14} weight="fill" />
        </MenuTrigger>
        <MenuPopup align="end">
          {can("run") && session.data && (
            <MenuItem onClick={() => setEditing(true)}>
              <PencilSimpleIcon size={14} aria-hidden="true" />
              {t("Edit session details")}
            </MenuItem>
          )}
          {level === "debug" && collapse && (
            <MenuItem onClick={() => collapse.setAll(!collapse.allCollapsed)}>
              {collapse.allCollapsed ? (
                <ArrowsOutLineVerticalIcon size={14} aria-hidden="true" />
              ) : (
                <ArrowsInLineVerticalIcon size={14} aria-hidden="true" />
              )}
              {collapse.allCollapsed
                ? t("Expand all runs")
                : t("Collapse all runs")}
            </MenuItem>
          )}
          {thread && (
            <MenuItem onClick={() => setMounts(true)}>
              <MonitorIcon size={14} aria-hidden="true" />
              {t("Environments and memory")}
            </MenuItem>
          )}
          {thread && can("run") && !thread.archived_at && (
            <MenuItem onClick={() => setExecution(true)}>
              <SlidersHorizontalIcon size={14} aria-hidden="true" />
              {t("Execution policy")}
            </MenuItem>
          )}
          {can("read") && (
            <MenuItem
              render={
                <Link to={`${basePath}/traces?session_id=${sessionId}`} />
              }
            >
              <ArrowSquareOutIcon size={14} aria-hidden="true" />
              {t("Open in traces")}
            </MenuItem>
          )}
          <MenuItem
            onClick={() => void navigator.clipboard?.writeText(sessionId)}
          >
            <CopyIcon size={14} aria-hidden="true" />
            {t("Copy session ID")}
          </MenuItem>
        </MenuPopup>
      </Menu>
      {thread && (
        <ThreadToolExecutionsDialog
          key={thread.id}
          threadId={thread.id}
          open={tools}
          onOpenChange={setTools}
        />
      )}
      {thread && (
        <ThreadMountsDialog
          thread={thread}
          open={mounts}
          onOpenChange={setMounts}
        />
      )}
      {execution && thread && (
        <ThreadExecutionDialog
          key={thread.id}
          thread={thread}
          onClose={() => setExecution(false)}
        />
      )}
      {editing && session.data && (
        <SessionEditor
          session={session.data}
          onClose={() => setEditing(false)}
        />
      )}
    </header>
  );
}

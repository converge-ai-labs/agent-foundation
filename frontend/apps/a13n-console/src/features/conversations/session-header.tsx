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
} from "@phosphor-icons/react";
import { Link, useParams } from "react-router";
import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { StatePill } from "../../shared/feedback";
import { AgentAvatar } from "../agents/avatar";
import { useAgent } from "../agents/queries";
import { isActiveRun, isConsoleSession } from "./api";
import { useRun, useSession } from "./queries";
import { useAnchoredLevel } from "./transcript/debug/view";
import { useRunCollapseAll } from "./transcript/debug/collapse";
import { useThreadRuns } from "./transcript/thread-runs";
import styles from "./conversations.module.css";

/**
 * Who answered, which session this is, and the one switch that changes how
 * much of it you see. Everything else about a run lives in the run itself.
 */
export function SessionHeader({
  threads,
}: {
  threads: readonly Schema["ThreadView"][];
}) {
  const { t } = useTranslation();
  const { sessionId = "", threadId = "", runId } = useParams();
  const { basePath, can } = useWorkspace();
  const thread = threads.find((entry) => entry.id === threadId);
  const root = threads.find(
    (entry) => entry.origin === "new" && entry.session_id === sessionId,
  );
  const session = useSession(sessionId);
  const { level, switchLevel } = useAnchoredLevel(thread, session.data);
  const run = useRun(runId);
  const valid = run.data?.session_id === sessionId;
  const agent = useAgent(valid ? run.data?.agent_id : undefined);
  const { runs } = useThreadRuns(threadId);
  const collapse = useRunCollapseAll();
  const debugSession = isConsoleSession(session.data);
  // Any Thread that branched from another reads under the Session's root.
  const child = !!thread && thread.origin !== "new";
  const active =
    valid &&
    !!run.data &&
    (isActiveRun(run.data.status) || run.data.status === "waiting");
  return (
    <header className={styles.sessionHeader}>
      <Link className={styles.back} to={`${basePath}/sessions`}>
        <CaretLeftIcon size={13} aria-hidden="true" />
        {t("Sessions")}
      </Link>
      <span className={styles.headerRule} aria-hidden="true" />
      <div className={styles.sessionIdentity}>
        {agent.data ? (
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
            {agent.data.name}
          </Link>
        ) : (
          <span className={styles.agentLink}>{t("Session")}</span>
        )}
        <span className={styles.subNote}>
          {debugSession
            ? [
                t("Debug session"),
                runs.length
                  ? t("{{count}} runs", { count: runs.length })
                  : null,
              ]
                .filter(Boolean)
                .join(" · ")
            : run.data
              ? t("Started by {{trigger}}", {
                  trigger: t(`trigger.${run.data.trigger}`, {
                    defaultValue: run.data.trigger,
                  }),
                })
              : ""}
        </span>
      </div>
      <div className={styles.sessionControls}>
        {active && run.data && <StatePill state={run.data.status} />}
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
        <SegmentedControl
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
      </div>
    </header>
  );
}

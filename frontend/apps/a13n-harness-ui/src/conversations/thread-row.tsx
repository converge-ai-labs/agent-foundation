import { useContext, useRef, useState } from "react";
import { NavLink, useNavigate, useLocation } from "react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, Menu, MenuTrigger, MenuPopup, MenuItem } from "a13n-ui";
import {
  ChatCircle,
  CaretRight,
  Question,
  CircleNotch,
  WarningCircleIcon,
  Archive,
  ArrowCounterClockwise,
  DotsThree,
  GitFork,
  PencilSimple,
  ShareNetwork,
  SlidersHorizontal,
  Star,
} from "@phosphor-icons/react";
import { result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import { ErrorNotice } from "../shell/ui";
import { refreshThreadLists } from "./queries";
import { refreshThread } from "./refresh";
import { refreshActivity } from "./activity-updates";
import { applyThreadMutation } from "./thread-updates";
import { ComposerDrafts } from "./composer";
import { conversationTitle } from "./local-input";
import { NewConversationDrafts, newConversationPath } from "./new-conversation";
import styles from "./conversation.module.css";
import { useResults } from "./results";
import { useUnsent } from "./unsent";
import { CoordinatorIcon } from "./coordinator-icon";
import {
  canPromoteCoordinator,
  CoordinatorPromotion,
  useCoordinatorMutation,
} from "./coordinator-settings";

function threadState(row: ActivityRow) {
  if (row.pending_decision) return "Needs your answer";
  if (row.thread.root_activity.state === "preparing") return "Preparing";
  if (row.thread.root_activity.state === "running") return "Running";
  if (row.thread.last_execution?.status === "failed") return "Failed";
  if (row.thread.last_execution?.status === "unknown") return "Outcome unknown";
  if (row.thread.archived) return "Archived";
  return "";
}

function ThreadStateIcon({ row }: { row: ActivityRow }) {
  if (row.pending_decision)
    return <Question className={styles.threadWaiting} aria-hidden="true" />;
  if (row.thread.root_activity.state !== "inactive")
    return <CircleNotch className={styles.threadRunning} aria-hidden="true" />;
  if (["failed", "unknown"].includes(row.thread.last_execution?.status ?? ""))
    return (
      <WarningCircleIcon className={styles.threadFailed} aria-hidden="true" />
    );
  return row.thread.archived ? (
    <Archive aria-hidden="true" />
  ) : (
    <ChatCircle aria-hidden="true" />
  );
}

type ActivityRow = Pick<Schema<"ThreadActivityView">, "thread"> &
  Partial<Schema<"ThreadActivityView">>;

export function ThreadRow({
  row,
  showRestore = false,
  showProject = false,
  activeWorkerCount = 0,
  disclosure,
}: {
  row: ActivityRow;
  showRestore?: boolean | "compact";
  showProject?: boolean;
  activeWorkerCount?: number;
  disclosure?: { expanded: boolean; onToggle: () => void };
}) {
  const coordinator = useCoordinatorMutation(row.thread);
  const [promoting, setPromoting] = useState(false);
  const { tracker: results } = useResults();
  const unsent = useUnsent().inputs.has(row.thread.thread_id);
  const unread = results?.isUnread(row.thread.thread_id);
  const navigate = useNavigate();
  const location = useLocation();
  const transport = useTransport();
  const queries = useQueryClient();
  const newDrafts = useContext(NewConversationDrafts);
  const composers = useContext(ComposerDrafts);
  const title = conversationTitle(
    row.thread,
    composers.get(row.thread.thread_id)?.localInputs,
  );
  const isCoordinator = row.thread.role === "coordinator";
  const state = threadState(row);
  const workerActivity =
    activeWorkerCount > 0
      ? `${activeWorkerCount} ${activeWorkerCount === 1 ? "worker" : "workers"} active`
      : "";
  const actionsButton = useRef<HTMLButtonElement>(null);
  const canStar = row.thread.role !== "worker" && !row.thread.parent_thread_id;
  const starLabel = row.thread.starred ? "Unstar chat" : "Star chat";
  const star = useMutation({
    mutationFn: () =>
      result(
        transport.client.PATCH("/api/threads/{thread_id}/metadata", {
          params: { path: { thread_id: row.thread.thread_id } },
          body: {
            expected_version: row.thread.metadata_version,
            patch: { starred: !row.thread.starred },
          },
        }),
      ),
    onSuccess: (updated) => applyThreadMutation(queries, updated),
    onSettled: () => {
      refreshThread(queries, row.thread.thread_id, "metadata");
      refreshActivity(queries, transport, row.thread.thread_id);
      if (document.activeElement === document.body)
        actionsButton.current?.focus();
    },
  });
  const toggleStar = () => {
    if (star.isPending) return;
    star.mutate();
  };
  const fork = useMutation({
    mutationFn: () =>
      result(
        transport.client.POST("/api/threads/{thread_id}/fork", {
          params: { path: { thread_id: row.thread.thread_id } },
        }),
      ),
    onSuccess: (copied) => {
      void refreshThreadLists(queries);
      navigate(`/threads/${encodeURIComponent(copied.thread_id)}`);
    },
  });
  const archive = useMutation({
    mutationFn: () =>
      result(
        transport.client.PATCH("/api/threads/{thread_id}/metadata", {
          params: { path: { thread_id: row.thread.thread_id } },
          body: {
            expected_version: row.thread.metadata_version,
            patch: { archived: !row.thread.archived },
          },
        }),
      ),
    onSuccess: () => {
      if (!row.thread.archived) newDrafts.detachArchived(row.thread.thread_id);
      if (
        !row.thread.archived &&
        location.pathname ===
          `/threads/${encodeURIComponent(row.thread.thread_id)}`
      )
        navigate(newConversationPath(row.thread.configuration.project_id));
    },
    onSettled: () => {
      refreshThread(queries, row.thread.thread_id, "metadata");
      void refreshThreadLists(queries);
      void queries.invalidateQueries({ queryKey: ["unsent-threads"] });
    },
  });
  return (
    <div>
      <div key={row.thread.thread_id} className={styles.threadRow}>
        <NavLink
          to={`/threads/${encodeURIComponent(row.thread.thread_id)}`}
          className={({ isActive }) =>
            `${styles.threadLink} ${isActive ? styles.selected : ""}`
          }
        >
          <span className={styles.threadLabel}>
            <strong title={title}>{title}</strong>
            {row.thread.role === "coordinator" && title !== "Coordinator" && (
              <span className={styles.srOnly}>Coordinator</span>
            )}
            {showProject && (
              <small>
                {row.project_name ??
                  (row.thread.configuration.project_id
                    ? "Unavailable project"
                    : "Without a project")}
              </small>
            )}
          </span>
          {isCoordinator && workerActivity && (
            <small
              className={styles.coordinatorActivity}
              title={workerActivity}
              aria-label={workerActivity}
            >
              {activeWorkerCount} active
            </small>
          )}
          {unsent && (
            <span
              className={styles.draftMarker}
              role="img"
              aria-label="Draft · Unsent input"
              title="Unsent input"
            >
              <PencilSimple size={12} aria-hidden="true" />
            </span>
          )}
          {state && (
            <span className={styles.threadStatus} title={state}>
              <ThreadStateIcon row={row} />
              <span className={styles.srOnly}>{state}</span>
            </span>
          )}
          {unread && (
            <span
              className={styles.resultDot}
              role="img"
              aria-label="New result"
              title="New result"
            />
          )}
        </NavLink>
        {showRestore && row.thread.archived && (
          <Button
            variant="ghost"
            size={showRestore === "compact" ? "icon-sm" : "sm"}
            title={`Restore ${title}`}
            loading={archive.isPending}
            disabled={row.thread.root_activity.state !== "inactive"}
            onClick={() => archive.mutate()}
            aria-label={`Restore ${title}`}
          >
            <ArrowCounterClockwise />
            {showRestore !== "compact" && "Restore"}
          </Button>
        )}
        {canStar && row.thread.starred && (
          <span
            className={styles.threadStar}
            role="img"
            aria-label={`Starred chat: ${title}`}
            title="Starred · Shared with everyone in this project"
          >
            <Star size={12} weight="fill" aria-hidden="true" />
          </span>
        )}
        <Menu>
          <MenuTrigger
            render={
              <Button ref={actionsButton} variant="ghost" size="icon-sm" />
            }
            className={styles.threadActions}
            aria-busy={star.isPending || fork.isPending}
            aria-label={`Actions for ${title}`}
          >
            <DotsThree />
          </MenuTrigger>
          <MenuPopup align="start" side="right">
            {row.thread.role === "coordinator" && (
              <MenuItem
                disabled={row.thread.archived}
                onClick={() =>
                  navigate(
                    newConversationPath(
                      row.thread.configuration.project_id,
                      row.thread.thread_id,
                    ),
                  )
                }
              >
                <ChatCircle /> New worker
              </MenuItem>
            )}
            {canStar && (
              <MenuItem disabled={star.isPending} onClick={toggleStar}>
                <Star weight={row.thread.starred ? "fill" : "regular"} />
                {starLabel}
              </MenuItem>
            )}
            {(
              [
                ["rename", "Rename chat", "Rename", PencilSimple],
                ["share", "Share chat", "Share", ShareNetwork],
                ["details", "Chat details", "Details", SlidersHorizontal],
              ] as const
            ).map(([action, label, caption, Icon]) => (
              <MenuItem
                key={action}
                aria-label={label}
                onClick={() =>
                  navigate(
                    `/threads/${encodeURIComponent(row.thread.thread_id)}?dialog=${action}`,
                  )
                }
              >
                <Icon />
                {caption}
              </MenuItem>
            ))}
            {row.thread.role === "ordinary" &&
              !row.thread.parent_thread_id &&
              !row.thread.memory_scope &&
              row.thread.configuration.agent_source.kind === "agent" && (
                <MenuItem
                  disabled={fork.isPending}
                  onClick={() => fork.mutate()}
                >
                  <GitFork /> Fork chat
                </MenuItem>
              )}
            {row.thread.role === "ordinary" &&
              row.thread.configuration.project_id && (
                <MenuItem
                  disabled={
                    !canPromoteCoordinator(row.thread, !!row.pending_decision)
                  }
                  onClick={() => setPromoting(true)}
                >
                  <CoordinatorIcon size={16} /> Make Coordinator
                </MenuItem>
              )}
            {row.thread.role === "coordinator" && (
              <MenuItem
                disabled={coordinator.isPending}
                onClick={() => coordinator.mutate(!row.thread.auto_followup)}
              >
                <CoordinatorIcon size={16} />
                {row.thread.auto_followup
                  ? "Pause follow-ups"
                  : "Enable follow-ups"}
              </MenuItem>
            )}
            {row.thread.configuration.agent_source.kind !== "live" && (
              <MenuItem
                disabled={
                  archive.isPending ||
                  row.thread.root_activity.state !== "inactive"
                }
                onClick={() => archive.mutate()}
              >
                <Archive />
                {row.thread.archived ? "Restore chat" : "Archive chat"}
              </MenuItem>
            )}
          </MenuPopup>
        </Menu>
        {disclosure && (
          <button
            type="button"
            className={styles.threadDisclosure}
            aria-label={`${disclosure.expanded ? "Collapse" : "Expand"} workers for ${title}`}
            aria-expanded={disclosure.expanded}
            onClick={disclosure.onToggle}
          >
            <CaretRight
              aria-hidden="true"
              className={
                disclosure.expanded ? styles.expandedChevron : undefined
              }
            />
          </button>
        )}
      </div>
      <ErrorNotice
        error={star.error || archive.error || fork.error || coordinator.error}
      />
      <CoordinatorPromotion
        open={promoting}
        close={() => setPromoting(false)}
        mutation={coordinator}
      />
    </div>
  );
}

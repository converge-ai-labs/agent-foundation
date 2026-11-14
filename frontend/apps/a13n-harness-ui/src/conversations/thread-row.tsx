import type { TFunction } from "i18next";
import { useTranslation } from "../i18n";
import { useContext, useEffect, useRef, useState } from "react";
import { NavLink, useNavigate, useLocation } from "react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, Menu, MenuTrigger, MenuPopup, MenuItem } from "a13n-ui";
import {
  ChatCircle,
  EnvelopeSimple,
  CaretRight,
  Question,
  CircleNotch,
  WarningCircleIcon,
  Archive,
  ArrowUp,
  ArrowDown,
  ArrowCounterClockwise,
  DotsThree,
  GitFork,
  PencilSimple,
  ShareNetwork,
  SlidersHorizontal,
  Star,
  Trash,
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
import { canDeleteThread, DeleteThread } from "./delete-thread";
import {
  canPromoteCoordinator,
  CoordinatorPromotion,
  useCoordinatorMutation,
} from "./coordinator-settings";

import { NavigationDragContext } from "./navigation-drag";

function threadState(row: ActivityRow, t: TFunction) {
  if (row.pending_decision) return t("Needs your answer");
  if (row.thread.root_activity.state === "preparing") return t("Preparing");
  if (row.thread.root_activity.state === "running") return t("Running");
  if (row.thread.last_execution?.status === "failed") return t("Failed");
  if (row.thread.last_execution?.status === "unknown")
    return t("Outcome unknown");
  if (row.thread.archived) return t("Archived");
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
  const { t } = useTranslation();
  const drag = useContext(NavigationDragContext);
  const rowElement = useRef<HTMLDivElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const press = useRef<{
    timer: ReturnType<typeof setTimeout>;
    x: number;
    y: number;
  } | null>(null);
  const suppressClick = useRef(false);
  const cancelPress = () => {
    if (press.current) clearTimeout(press.current.timer);
    press.current = null;
  };
  useEffect(() => cancelPress, []);
  const sortable =
    !!drag &&
    !row.thread.archived &&
    row.thread.role !== "worker" &&
    !row.thread.parent_thread_id;
  const coordinator = useCoordinatorMutation(row.thread);
  const [promoting, setPromoting] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const { tracker: results } = useResults();
  const unsent = useUnsent().inputs.has(row.thread.thread_id);
  const reading = results
    ?.getSnapshot()
    .threads.get(row.thread.thread_id)?.thread;
  const unread =
    reading &&
    (reading.read_state_version ?? 0) >= (row.thread.read_state_version ?? 0)
      ? reading.unread
      : row.thread.unread;
  const navigate = useNavigate();
  const location = useLocation();
  const transport = useTransport();
  const queries = useQueryClient();
  const newDrafts = useContext(NewConversationDrafts);
  const composers = useContext(ComposerDrafts);
  const title = conversationTitle(
    row.thread,
    composers.get(row.thread.thread_id)?.localInputs,
    t("Untitled chat"),
  );
  const isCoordinator = row.thread.role === "coordinator";
  const state = threadState(row, t);
  const workerActivity =
    activeWorkerCount > 0
      ? t("Active worker count", { count: activeWorkerCount })
      : "";
  const actionsButton = useRef<HTMLButtonElement>(null);
  const canStar = row.thread.role !== "worker" && !row.thread.parent_thread_id;
  const starLabel = row.thread.starred ? t("Unstar chat") : t("Star chat");
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
  const markUnread = useMutation({
    mutationFn: () =>
      result(
        transport.client.POST("/api/threads/{thread_id}/unread", {
          params: { path: { thread_id: row.thread.thread_id } },
        }),
      ),
    onSuccess: (updated) => {
      results?.observe(updated);
      applyThreadMutation(queries, updated);
      refreshThread(queries, updated.thread_id, "metadata");
    },
    onSettled: () => {
      if (document.activeElement === document.body)
        actionsButton.current?.focus();
    },
  });
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
      <div
        ref={rowElement}
        key={row.thread.thread_id}
        className={styles.threadRow}
        data-menu-open={menuOpen || undefined}
        onContextMenu={(event) => {
          event.preventDefault();
          cancelPress();
          setMenuOpen(true);
        }}
        onPointerDown={(event) => {
          if (
            event.pointerType !== "touch" ||
            !event.isPrimary ||
            (event.target as HTMLElement).closest("button")
          )
            return;
          cancelPress();
          suppressClick.current = false;
          press.current = {
            x: event.clientX,
            y: event.clientY,
            timer: setTimeout(() => {
              suppressClick.current = true;
              press.current = null;
              setMenuOpen(true);
            }, 500),
          };
        }}
        onPointerMove={(event) => {
          if (
            press.current &&
            Math.hypot(
              event.clientX - press.current.x,
              event.clientY - press.current.y,
            ) > 8
          )
            cancelPress();
        }}
        onPointerUp={() => {
          cancelPress();
          // Suppress the release click, not a later deliberate menu action.
          setTimeout(() => {
            suppressClick.current = false;
          }, 0);
        }}
        onPointerCancel={() => {
          cancelPress();
          suppressClick.current = false;
        }}
        onPointerLeave={cancelPress}
        onClickCapture={(event) => {
          if (!suppressClick.current) return;
          suppressClick.current = false;
          event.preventDefault();
          event.stopPropagation();
        }}
        data-nav-thread={sortable ? row.thread.thread_id : undefined}
        data-nav-source={drag?.moving === row.thread.thread_id || undefined}
      >
        {disclosure && (
          <button
            type="button"
            className={styles.threadDisclosure}
            aria-label={
              disclosure.expanded
                ? t("Collapse workers for {{title}}", { title })
                : t("Expand workers for {{title}}", { title })
            }
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
        <NavLink
          draggable={false}
          onPointerDown={
            sortable
              ? (event) =>
                  drag.begin(event, {
                    kind: "thread",
                    id: row.thread.thread_id,
                    label: title,
                    thread: row.thread,
                  })
              : undefined
          }
          onKeyDown={
            sortable
              ? (event) => {
                  if (
                    event.altKey &&
                    ["ArrowUp", "ArrowDown"].includes(event.key)
                  ) {
                    event.preventDefault();
                    drag.moveThread(
                      row.thread,
                      rowElement.current!,
                      event.key === "ArrowUp" ? -1 : 1,
                    );
                    requestAnimationFrame(() =>
                      rowElement.current
                        ?.querySelector<HTMLElement>("a")
                        ?.focus(),
                    );
                  }
                }
              : undefined
          }
          aria-keyshortcuts={sortable ? "Alt+ArrowUp Alt+ArrowDown" : undefined}
          to={`/threads/${encodeURIComponent(row.thread.thread_id)}`}
          className={({ isActive }) =>
            `${styles.threadLink} ${isActive ? styles.selected : ""}`
          }
        >
          <span className={styles.threadLabel}>
            <strong title={title}>{title}</strong>
            {row.thread.role === "coordinator" &&
              title !== t("Coordinator") && (
                <span className={styles.srOnly}>{t("Coordinator")}</span>
              )}
            {showProject && (
              <small>
                {row.project_name ??
                  (row.thread.configuration.project_id
                    ? t("Unavailable project")
                    : t("Without a project"))}
              </small>
            )}
          </span>
          {isCoordinator && workerActivity && (
            <small
              className={styles.coordinatorActivity}
              title={workerActivity}
              aria-label={workerActivity}
            >
              {t("{{count, number}} active", { count: activeWorkerCount })}
            </small>
          )}
          {unsent && (
            <span
              className={styles.draftMarker}
              role="img"
              aria-label={t("Draft - Unsent input")}
              title={t("Unsent input")}
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
              aria-label={t("New result")}
              title={t("New result")}
            />
          )}
        </NavLink>
        {showRestore && row.thread.archived && (
          <Button
            variant="ghost"
            size={showRestore === "compact" ? "icon-sm" : "sm"}
            title={t("Restore {{title}}", { title })}
            loading={archive.isPending}
            disabled={row.thread.root_activity.state !== "inactive"}
            onClick={() => archive.mutate()}
            aria-label={t("Restore {{title}}", { title })}
          >
            <ArrowCounterClockwise />
            {showRestore !== "compact" && t("Restore")}
          </Button>
        )}
        {canStar && row.thread.starred && (
          <span
            className={styles.threadStar}
            role="img"
            aria-label={t("Starred chat: {{title}}", { title })}
            title={t("Starred - Shared with everyone in this project")}
          >
            <Star size={12} weight="fill" aria-hidden="true" />
          </span>
        )}
        <Menu open={menuOpen} onOpenChange={setMenuOpen}>
          <MenuTrigger
            render={
              <Button ref={actionsButton} variant="ghost" size="icon-sm" />
            }
            className={styles.threadActions}
            aria-busy={star.isPending || fork.isPending || markUnread.isPending}
            aria-label={t("Actions for {{name}}", { name: title })}
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
                <ChatCircle />
                {t("New worker")}
              </MenuItem>
            )}
            {sortable && (
              <>
                <MenuItem
                  onClick={() =>
                    drag.moveThread(row.thread, rowElement.current!, -1)
                  }
                >
                  <ArrowUp />
                  {t("Move up")}
                </MenuItem>
                <MenuItem
                  onClick={() =>
                    drag.moveThread(row.thread, rowElement.current!, 1)
                  }
                >
                  <ArrowDown />
                  {t("Move down")}
                </MenuItem>
              </>
            )}
            {!row.thread.parent_thread_id && !row.thread.memory_scope && (
              <MenuItem
                disabled={markUnread.isPending}
                onClick={() => {
                  if (!markUnread.isPending) markUnread.mutate();
                }}
              >
                <EnvelopeSimple />
                {t("Mark unread")}
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
                ["rename", t("Rename chat"), t("Rename"), PencilSimple],
                ["share", t("Share chat"), t("Share"), ShareNetwork],
                ["details", t("Chat details"), t("Details"), SlidersHorizontal],
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
                  <GitFork />
                  {t("Fork chat")}
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
                  <CoordinatorIcon size={16} />
                  {t("Make Coordinator")}
                </MenuItem>
              )}
            {row.thread.role === "coordinator" && (
              <MenuItem
                disabled={coordinator.isPending}
                onClick={() => coordinator.mutate(!row.thread.auto_followup)}
              >
                <CoordinatorIcon size={16} />
                {row.thread.auto_followup
                  ? t("Pause follow-ups")
                  : t("Enable follow-ups")}
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
                {row.thread.archived ? t("Restore chat") : t("Archive chat")}
              </MenuItem>
            )}
            {canDeleteThread(row.thread) && (
              <MenuItem
                variant="destructive"
                disabled={row.thread.root_activity.state !== "inactive"}
                onClick={() => setDeleting(true)}
              >
                <Trash />
                {t("Delete chat")}
              </MenuItem>
            )}
          </MenuPopup>
        </Menu>
      </div>
      <ErrorNotice
        error={
          markUnread.error ||
          star.error ||
          archive.error ||
          fork.error ||
          coordinator.error
        }
      />
      {deleting && (
        <DeleteThread thread={row.thread} close={() => setDeleting(false)} />
      )}
      <CoordinatorPromotion
        open={promoting}
        close={() => setPromoting(false)}
        mutation={coordinator}
      />
    </div>
  );
}

import { Button, Menu, MenuItem, MenuPopup, MenuTrigger } from "a13n-ui";
import { CaretDownIcon, CheckIcon, GitBranchIcon } from "@phosphor-icons/react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useClient } from "../../auth/context";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { useThreadNavigation } from "./queries";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { Link, useLocation } from "react-router";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { conversationQueries, runPath, type ViewLevel } from "./api";
import { chronological } from "./transcript/thread-runs";
import styles from "./fork.module.css";

type Thread = Schema["ThreadView"];

/** Names use global positions from the navigation read, never a loaded page's length. */
export function threadName(thread: Schema["ThreadReference"], t: TFunction) {
  if (thread.origin === "child") return thread.subagent ?? t("Child thread");
  return thread.origin === "fork"
    ? t("Branch {{index}}", { index: thread.origin_number })
    : thread.origin_count === 1
      ? t("Main thread")
      : t("Thread {{index}}", { index: thread.origin_number });
}

/** Switch independent conversations without changing the Chat/Debug level. */
export function ThreadBranches({
  thread,
  level,
}: {
  thread: Thread;
  level: ViewLevel;
}) {
  const { t } = useTranslation();
  const { basePath, workspace } = useWorkspace();
  const client = useClient();
  const selected = useThreadNavigation(thread.session_id, thread.id);
  const { search } = useLocation();
  const params = new URLSearchParams(search);
  params.set("view", level);
  const [open, setOpen] = useState(false);
  const pages = useInfiniteQuery({
    ...conversationQueries(client, workspace.id).threadPages(
      thread.session_id,
      { origin: ["new", "fork"] },
    ),
    enabled: open,
  });
  const listed = pages.data?.pages.flatMap((page) => page.items) ?? [];
  const conversations = chronological([
    ...listed,
    ...(selected.data &&
    !listed.some((thread) => thread.id === selected.data?.id)
      ? [selected.data]
      : []),
  ]);
  return (
    <Menu open={open} onOpenChange={setOpen}>
      <MenuTrigger
        render={
          <Button
            size="sm"
            variant="ghost"
            className={styles.branchTrigger}
            aria-label={t("Switch thread")}
          />
        }
      >
        <GitBranchIcon size={14} aria-hidden="true" />
        <span>
          {selected.data
            ? threadName(selected.data, t)
            : t(thread.origin === "fork" ? "Branch" : "Thread")}
        </span>
        <CaretDownIcon size={11} aria-hidden="true" />
      </MenuTrigger>
      <MenuPopup align="end" className={styles.branchMenu}>
        <ErrorNotice
          error={pages.error ?? selected.error}
          retry={() => {
            void pages.refetch();
            if (selected.error) void selected.refetch();
          }}
        />
        {pages.isPending && <Loading variant="list" rows={2} />}
        {conversations.map((entry) => {
          const origin = entry.origin_thread;
          return (
            <MenuItem
              key={entry.id}
              render={
                <Link
                  to={`${basePath}/sessions/${entry.session_id}/threads/${entry.id}?${params}`}
                  aria-current={entry.id === thread.id ? "page" : undefined}
                />
              }
            >
              <GitBranchIcon size={14} aria-hidden="true" />
              <span className={styles.branchLabel}>
                <span>{threadName(entry, t)}</span>{" "}
                {origin && (
                  <small>
                    {t("From {{thread}}", {
                      thread: threadName(origin, t),
                    })}
                  </small>
                )}
              </span>
              {entry.id === thread.id && (
                <CheckIcon size={14} aria-hidden="true" />
              )}
            </MenuItem>
          );
        })}
        {pages.hasNextPage && (
          <MenuItem
            closeOnClick={false}
            disabled={pages.isFetchingNextPage}
            onClick={() => void pages.fetchNextPage()}
          >
            {t("Load more")}
          </MenuItem>
        )}
      </MenuPopup>
    </Menu>
  );
}

/** The exact Run this branch inherited, including when it forked another branch. */
export function BranchOrigin({
  thread,
  level,
}: {
  thread: Thread;
  level: ViewLevel;
}) {
  const { t } = useTranslation();
  const { basePath } = useWorkspace();
  const selected = useThreadNavigation(
    thread.session_id,
    thread.origin === "fork" ? thread.id : undefined,
  );
  if (
    thread.origin !== "fork" ||
    !thread.origin_run_id ||
    !thread.origin_thread_id
  )
    return null;
  const origin = selected.data?.origin_thread;
  return (
    <aside className={styles.origin} aria-label={t("Branch origin")}>
      <GitBranchIcon size={16} aria-hidden="true" />
      <div className={styles.originText}>
        <strong>
          {selected.data
            ? threadName(selected.data, t)
            : t(thread.origin === "fork" ? "Branch" : "Thread")}
        </strong>
        <p className={styles.note}>
          {t("History is inherited through the original run.")}
        </p>
      </div>
      <Link
        to={runPath(
          basePath,
          {
            session_id: thread.session_id,
            thread_id: thread.origin_thread_id,
            run_id: thread.origin_run_id,
          },
          level,
        )}
      >
        {origin
          ? t("View origin in {{thread}}", {
              thread: threadName(origin, t),
            })
          : t("View original run")}
      </Link>
    </aside>
  );
}

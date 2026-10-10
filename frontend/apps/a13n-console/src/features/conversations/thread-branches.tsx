import { Button, Menu, MenuItem, MenuPopup, MenuTrigger } from "a13n-ui";
import { CaretDownIcon, CheckIcon, GitBranchIcon } from "@phosphor-icons/react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { Link, useLocation } from "react-router";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { runPath, type ViewLevel } from "./api";
import { chronological } from "./transcript/thread-runs";
import styles from "./fork.module.css";

type Thread = Schema["ThreadView"];

/** Names are derived from creation order, independent of recent activity. */
function threadName(thread: Thread, threads: readonly Thread[], t: TFunction) {
  if (thread.origin === "child") return thread.subagent ?? t("Child thread");
  const peers = chronological(
    threads.filter((entry) => entry.origin === thread.origin),
  );
  const index = peers.findIndex((entry) => entry.id === thread.id) + 1;
  if (thread.origin === "fork")
    return index ? t("Branch {{index}}", { index }) : t("Branch");
  return peers.length === 1
    ? t("Main conversation")
    : t("Conversation {{index}}", { index });
}

/** Switch independent conversations without changing the Chat/Debug level. */
export function ThreadBranches({
  thread,
  threads,
  level,
}: {
  thread: Thread;
  threads: readonly Thread[];
  level: ViewLevel;
}) {
  const { t } = useTranslation();
  const { basePath } = useWorkspace();
  const { search } = useLocation();
  const params = new URLSearchParams(search);
  params.set("view", level);
  const conversations = chronological(threads).filter(
    (entry) => entry.origin !== "child" || entry.id === thread.id,
  );
  return (
    <Menu>
      <MenuTrigger
        render={
          <Button
            size="sm"
            variant="ghost"
            className={styles.branchTrigger}
            aria-label={t("Switch conversation")}
          />
        }
      >
        <GitBranchIcon size={14} aria-hidden="true" />
        <span>{threadName(thread, threads, t)}</span>
        <CaretDownIcon size={11} aria-hidden="true" />
      </MenuTrigger>
      <MenuPopup align="end" className={styles.branchMenu}>
        {conversations.map((entry) => {
          const origin = threads.find(
            (item) => item.id === entry.origin_thread_id,
          );
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
                <span>{threadName(entry, threads, t)}</span>{" "}
                {origin && (
                  <small>
                    {t("From {{conversation}}", {
                      conversation: threadName(origin, threads, t),
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
      </MenuPopup>
    </Menu>
  );
}

/** The exact Run this branch inherited, including when it forked another branch. */
export function BranchOrigin({
  thread,
  threads,
  level,
}: {
  thread: Thread;
  threads: readonly Thread[];
  level: ViewLevel;
}) {
  const { t } = useTranslation();
  const { basePath } = useWorkspace();
  if (
    thread.origin !== "fork" ||
    !thread.origin_run_id ||
    !thread.origin_thread_id
  )
    return null;
  const origin = threads.find((entry) => entry.id === thread.origin_thread_id);
  return (
    <aside className={styles.origin} aria-label={t("Branch origin")}>
      <GitBranchIcon size={16} aria-hidden="true" />
      <div className={styles.originText}>
        <strong>{threadName(thread, threads, t)}</strong>
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
          ? t("View origin in {{conversation}}", {
              conversation: threadName(origin, threads, t),
            })
          : t("View original run")}
      </Link>
    </aside>
  );
}

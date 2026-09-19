import { useQueries } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";
import { Button, TooltipProvider } from "a13n-ui";
import { Panel } from "../../../shared/page";
import {
  CaretRightIcon,
  GitBranchIcon,
  StackIcon,
  TreeStructureIcon,
} from "@phosphor-icons/react";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import type { Schema } from "../../../shared/api";
import { ErrorNotice, Loading } from "../../../shared/feedback";
import { conversationQueries } from "../api";
import { RunGroup, groupRuns } from "./session-map-groups";
import { MapRun } from "./session-map-run";
import styles from "./session-map.module.css";

type Thread = Schema["ThreadResource"];
const chronological = <T extends { created_at: string; id: string }>(
  entries: readonly T[],
) =>
  [...entries].sort(
    (a, b) =>
      a.created_at.localeCompare(b.created_at) || a.id.localeCompare(b.id),
  );

export function SessionMap({
  threads: unsorted,
  onClose,
  width,
  onWidthChange,
  resizable,
}: {
  threads: readonly Thread[];
  onClose: () => void;
  width?: number;
  onWidthChange?: (width: number) => void;
  resizable?: boolean;
}) {
  const threads = chronological(unsorted);
  const { t } = useTranslation();
  const { threadId } = useParams();
  const client = useClient(),
    { workspace, basePath } = useWorkspace();
  const queries = conversationQueries(client, workspace.id);
  const runs = useQueries({
    queries: threads.map((thread) => queries.runs(thread.id)),
  });
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const scroll = useRef<HTMLDivElement>(null);
  const ready = runs.every((query) => !query.isPending);
  useEffect(() => {
    if (ready)
      scroll.current
        ?.querySelector('[aria-current="page"]')
        ?.scrollIntoView({ block: "center" });
  }, [ready]);
  const ids = new Set(threads.map((thread) => thread.id));
  const roots = threads.filter(
    (thread) => !thread.origin_thread_id || !ids.has(thread.origin_thread_id),
  );
  const total =
    ready && runs.every((query) => !query.isError)
      ? runs.reduce((sum, query) => sum + (query.data?.length ?? 0), 0)
      : null;
  function threadNode(thread: Thread) {
    const index = threads.findIndex((entry) => entry.id === thread.id);
    const query = runs[index]!;
    const entries = chronological(query.data ?? []);
    const children = threads.filter(
      (entry) => entry.origin_thread_id === thread.id,
    );
    const expanded = !collapsed.has(thread.id);
    const title = `${t("Thread")} ${index + 1}`;
    return (
      <li key={thread.id} className={styles.thread}>
        <div className={styles.threadButton}>
          <Button
            variant="ghost"
            size="icon-sm"
            className={styles.threadToggle}
            aria-label={t("Expand or collapse {{thread}}", { thread: title })}
            aria-expanded={expanded}
            onClick={() =>
              setCollapsed((previous) => {
                const next = new Set(previous);
                if (next.has(thread.id)) next.delete(thread.id);
                else next.add(thread.id);
                return next;
              })
            }
          >
            <CaretRightIcon
              className={styles.caret}
              data-expanded={expanded}
              size={13}
            />
          </Button>
          <Link
            className={styles.threadLink}
            to={`${basePath}/sessions/${thread.session_id}/threads/${thread.id}`}
            title={thread.id}
          >
            {thread.origin_kind === "fork" ? (
              <GitBranchIcon size={16} />
            ) : (
              <StackIcon size={16} />
            )}
            <span className={styles.threadName}>{title}</span>
            {thread.origin_kind === "fork" && (
              <span className={styles.branch}>{t("Branch")}</span>
            )}
            <span className={styles.count}>
              {query.data
                ? t("{{count}} runs", { count: query.data.length })
                : "—"}
            </span>
            {thread.id === threadId && (
              <span
                className={styles.currentDot}
                aria-label={t("Current thread")}
              />
            )}
          </Link>
        </div>
        {expanded && (
          <div className={styles.threadBody}>
            <ErrorNotice
              error={query.error}
              retry={() => void query.refetch()}
            />
            {query.isPending && <Loading variant="list" rows={3} />}
            {query.data?.length === 0 && (
              <p className={styles.empty}>{t("No runs yet")}</p>
            )}
            <ol className={styles.runs}>
              {groupRuns(
                entries,
                new Set(
                  children.flatMap((child) =>
                    child.origin_run_id ? [child.origin_run_id] : [],
                  ),
                ),
              ).map((group) => {
                const nodes = group.runs.map((run, offset) => {
                  const branches = children.filter(
                    (child) => child.origin_run_id === run.id,
                  );
                  return (
                    <MapRun
                      key={run.id}
                      run={run}
                      index={group.start + offset}
                      branchPoint={branches.length > 0}
                    >
                      {branches.length > 0 && (
                        <ul className={styles.branches}>
                          {branches.map(threadNode)}
                        </ul>
                      )}
                    </MapRun>
                  );
                });
                return children.length > 0 && group.runs.length > 1 ? (
                  <RunGroup
                    key={group.runs[0]!.id}
                    runs={group.runs}
                    start={group.start}
                  >
                    {nodes}
                  </RunGroup>
                ) : (
                  nodes
                );
              })}
            </ol>
            {children
              .filter(
                (child) =>
                  !entries.some((run) => run.id === child.origin_run_id),
              )
              .map((child) => (
                <ul className={styles.branches} key={child.id}>
                  <li className={styles.origin}>
                    {t("Origin run")}: {child.origin_run_id?.slice(-8) ?? "—"}
                  </li>
                  {threadNode(child)}
                </ul>
              ))}
          </div>
        )}
      </li>
    );
  }
  return (
    <Panel
      inline
      open
      resizable={resizable}
      width={width}
      onWidthChange={onWidthChange}
      onClose={onClose}
      label={t("Session map")}
      title={
        <>
          <TreeStructureIcon size={15} aria-hidden="true" />
          <strong>{t("Session map")}</strong>
          <span className={styles.summary}>
            {`${t("{{count}} threads", { count: threads.length })} · ${
              total === null ? "—" : t("{{count}} runs", { count: total })
            }`}
          </span>
        </>
      }
    >
      <TooltipProvider delay={250} closeDelay={100}>
        <div ref={scroll} className={styles.scroll}>
          <nav aria-label={t("Session map")}>
            <ul className={styles.tree}>{roots.map(threadNode)}</ul>
          </nav>
        </div>
      </TooltipProvider>
    </Panel>
  );
}

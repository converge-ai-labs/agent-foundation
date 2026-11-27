import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowSquareOut,
  GitBranch,
  LinkSimple,
  LinkSimpleBreak,
} from "@phosphor-icons/react";
import {
  Badge,
  Button,
  Popover,
  PopoverPopup,
  PopoverTitle,
  PopoverTrigger,
} from "a13n-ui";
import { useTranslation } from "../i18n";
import { ErrorNotice } from "../shell/ui";
import { scheduleRefresh } from "../conversations/refresh";
import { result, type Schema } from "../transport/client";
import { useStatus, useTransport } from "../transport/context";
import styles from "./thread-links.module.css";

export const threadLinksKey = (threadId: string) =>
  ["thread-links", threadId] as const;

export function useThreadLinks(threadId?: string) {
  const { client, shareId } = useTransport();
  return useQuery({
    queryKey: threadLinksKey(threadId ?? ""),
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/threads/{thread_id}/links", {
          params: { path: { thread_id: threadId! } },
          signal,
        }),
      ),
    enabled: !!threadId && !shareId,
    refetchOnWindowFocus: false,
    retry: false,
  });
}

export function useWorktreeMode(path: string | undefined) {
  const { client, shareId } = useTransport();
  const hostGit = useStatus().data?.features?.host_git;
  const query = useQuery({
    queryKey: ["native", "worktree-mode", path],
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/host/git/repository", {
          params: { query: { path: path! } },
          signal,
        }),
      ),
    enabled: !!path && !!hostGit && !shareId,
    refetchOnWindowFocus: false,
    retry: false,
  });
  return !!path && !!hostGit && !shareId && query.data?.state === "repository";
}

const statusBadge = {
  open: ["GitHub Open", "success"],
  draft: ["Draft", "secondary"],
  merged: ["Merged", "info"],
  closed: ["Closed", "secondary"],
  answered: ["Answered", "success"],
} as const;

export function ThreadLinks({
  threadId,
  data,
  openChanges,
  openFiles,
  openTerminal,
}: {
  threadId: string;
  data?: Schema<"ThreadLinksView">;
  openChanges?: (path: string) => void;
  openFiles?: (path: string) => void;
  openTerminal?: (path: string) => void;
}) {
  const { t } = useTranslation();
  const { client, shareId } = useTransport();
  const query = useQueryClient();
  const [open, setOpen] = useState(false);
  const unlink = useMutation({
    mutationFn: (body: Schema<"UnlinkThreadResource">) =>
      client.DELETE("/api/threads/{thread_id}/links", {
        params: { path: { thread_id: threadId } },
        body,
      }),
    onSuccess: () =>
      scheduleRefresh(
        query,
        (candidate) =>
          candidate.queryKey[0] === "thread-links" &&
          candidate.queryKey[1] === threadId,
        0,
      ),
  });
  if (shareId || !data || !(data.worktrees.length || data.urls.length))
    return null;
  const unlinkAction = (kind: "url" | "worktree", target: string) => (
    <Button
      size="icon-sm"
      variant="ghost"
      aria-label={t("Unlink")}
      title={t("Unlink")}
      disabled={unlink.isPending}
      onClick={() => unlink.mutate({ kind, target })}
    >
      <LinkSimpleBreak />
    </Button>
  );
  const worktreeAction = (
    label: string,
    callback: ((path: string) => void) | undefined,
    path: string,
  ) =>
    callback && (
      <Button
        variant="ghost"
        size="sm"
        onClick={() => {
          setOpen(false);
          callback(path);
        }}
      >
        {label}
      </Button>
    );
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={<Button variant="ghost" size="sm" className={styles.trigger} />}
        aria-label={t("Thread links")}
        title={t("Thread links")}
      >
        <LinkSimple />
        <span aria-hidden="true">
          {data.worktrees.length + data.urls.length}
        </span>
      </PopoverTrigger>
      <PopoverPopup align="end" className={styles.popup}>
        <PopoverTitle className={styles.srOnly}>
          {t("Thread links")}
        </PopoverTitle>
        <div className={styles.groups}>
          {unlink.error && <ErrorNotice inline error={unlink.error} />}
          {!!data.worktrees.length && (
            <section>
              <h2>{t("Worktrees")}</h2>
              <ul>
                {data.worktrees.map((link) => {
                  const name =
                    link.path.split(/[/\\]/).filter(Boolean).at(-1) ??
                    link.path;
                  const available =
                    link.state === "ready" || link.state === "detached";
                  return (
                    <li key={link.path} className={styles.worktree}>
                      <div className={styles.row}>
                        <GitBranch className={styles.icon} aria-hidden />
                        <div className={styles.identity}>
                          <span className={styles.name} title={link.path}>
                            {link.label ? `${link.label} · ${name}` : name}
                          </span>
                          <span
                            className={styles.detail}
                            title={link.head_oid ?? link.branch ?? undefined}
                          >
                            {link.branch ??
                              (link.state === "detached"
                                ? t("Detached HEAD") +
                                  (link.head_oid
                                    ? ` · ${link.head_oid.slice(0, 7)}`
                                    : "")
                                : link.state === "removed"
                                  ? t("Removed")
                                  : t("Status unavailable"))}
                          </span>
                        </div>
                        {!link.implicit && unlinkAction("worktree", link.path)}
                      </div>
                      {link.state !== "removed" &&
                        (openChanges || openFiles || openTerminal) && (
                          <div className={styles.actions}>
                            {available &&
                              worktreeAction(
                                t("Changes"),
                                openChanges,
                                link.path,
                              )}
                            {worktreeAction(t("Files"), openFiles, link.path)}
                            {worktreeAction(
                              t("Terminal"),
                              openTerminal,
                              link.path,
                            )}
                          </div>
                        )}
                    </li>
                  );
                })}
              </ul>
            </section>
          )}
          {!!data.urls.length && (
            <section>
              <h2>{t("Links")}</h2>
              <ul>
                {data.urls.map((link) => {
                  const host = new URL(link.url).hostname;
                  const artifact = link.github;
                  const badge =
                    artifact?.status && statusBadge[artifact.status];
                  const name = artifact?.title || link.label || host;
                  return (
                    <li key={link.url} className={styles.row}>
                      <LinkSimple className={styles.icon} aria-hidden />
                      <a
                        href={link.url}
                        target="_blank"
                        rel="noreferrer"
                        className={styles.identity}
                      >
                        <span className={styles.name} title={link.url}>
                          {name}
                          <ArrowSquareOut aria-hidden />
                        </span>
                        {(artifact || name !== host) && (
                          <span className={styles.detail}>
                            {artifact ? `#${artifact.number}` : host}
                            {badge && (
                              <Badge size="sm" variant={badge[1]}>
                                {t(badge[0])}
                              </Badge>
                            )}
                          </span>
                        )}
                      </a>
                      {unlinkAction("url", link.url)}
                    </li>
                  );
                })}
              </ul>
            </section>
          )}
        </div>
      </PopoverPopup>
    </Popover>
  );
}

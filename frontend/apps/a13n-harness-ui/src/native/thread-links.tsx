import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowSquareOut,
  ChatsCircle,
  CheckCircle,
  Folder,
  GitBranch,
  GitDiff,
  GitMerge,
  GitPullRequest,
  LinkSimple,
  LinkSimpleBreak,
  Record,
  Terminal,
} from "@phosphor-icons/react";
import { Badge, Button, ScrollArea } from "a13n-ui";
import { useTranslation } from "../i18n";
import { ErrorNotice } from "../shell/ui";
import { scheduleRefresh } from "../conversations/refresh";
import { useBackground } from "../conversations/background-context";
import {
  sectionTarget,
  SECTION_LABELS,
  type LinkSection,
} from "../conversations/background-work";
import { ViewLink } from "./view-link";
import { result, type Schema } from "../transport/client";
import { useStatus, useTransport } from "../transport/context";
import backgroundStyles from "../conversations/background.module.css";
import styles from "./thread-links.module.css";

export const threadLinksKey = (threadId: string) =>
  ["thread-links", threadId] as const;

export type ThreadLinkActions = {
  openChanges?: (path: string) => void;
  openFiles?: (path: string) => void;
  openTerminal?: (path: string) => void;
};

export function threadLinksLists(data?: Schema<"ThreadLinksView">) {
  return {
    worktrees: Array.isArray(data?.worktrees) ? data.worktrees : [],
    urls: Array.isArray(data?.urls) ? data.urls : [],
  };
}

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

function useUnlinkThreadResource(threadId: string) {
  const { client } = useTransport();
  const query = useQueryClient();
  return useMutation({
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
}

const statusBadge = {
  open: ["GitHub Open", "success"],
  draft: ["Draft", "secondary"],
  merged: ["Merged", "info"],
  closed: ["Closed", "secondary"],
  answered: ["Answered", "success"],
} as const;

function pathBasename(path: string) {
  return path.split(/[/\\]/).filter(Boolean).at(-1) ?? path;
}

function githubRepoSlug(url: string) {
  const parts = new URL(url).pathname.split("/").filter(Boolean);
  return parts.length >= 2 ? `${parts[0]}/${parts[1]}` : null;
}

function UrlIcon({
  artifact,
  className,
}: {
  artifact?: Schema<"GitHubLinkView"> | null;
  className?: string;
}) {
  const tone =
    artifact?.status === "open" || artifact?.status === "answered"
      ? styles.toneSuccess
      : artifact?.status === "merged"
        ? styles.toneInfo
        : undefined;
  const classes = [className, tone].filter(Boolean).join(" ") || undefined;
  if (artifact?.kind === "pr") {
    return artifact.status === "merged" ? (
      <GitMerge className={classes} aria-hidden />
    ) : (
      <GitPullRequest className={classes} aria-hidden />
    );
  }
  if (artifact?.kind === "issue") {
    return artifact.status === "closed" ? (
      <CheckCircle className={classes} aria-hidden />
    ) : (
      <Record className={classes} aria-hidden />
    );
  }
  if (artifact?.kind === "discussion") {
    return <ChatsCircle className={classes} aria-hidden />;
  }
  return <LinkSimple className={classes} aria-hidden />;
}

function WorktreeItems({
  items,
  actions,
  unlinkPending,
  onUnlink,
}: {
  items: Schema<"ThreadLinksView">["worktrees"];
  actions?: ThreadLinkActions;
  unlinkPending: boolean;
  onUnlink: (path: string) => void;
}) {
  const { t } = useTranslation();
  const worktreeAction = (
    label: string,
    Icon: typeof GitDiff,
    callback: ((path: string) => void) | undefined,
    path: string,
  ) =>
    callback && (
      <Button
        variant="ghost"
        size="sm"
        className={styles.actionButton}
        onClick={() => callback(path)}
      >
        <Icon aria-hidden />
        <span>{label}</span>
      </Button>
    );
  return (
    <ul className={styles.list}>
      {items.map((link) => {
        const name = pathBasename(link.path);
        const available = link.state === "ready" || link.state === "detached";
        return (
          <li key={link.path} className={styles.worktree}>
            <div className={styles.row}>
              <GitBranch className={styles.icon} aria-hidden />
              <div className={styles.identity}>
                <span className={styles.name} title={link.path}>
                  <span className={styles.nameText}>
                    {link.label ? `${link.label} · ${name}` : name}
                  </span>
                </span>
                <span
                  className={styles.detail}
                  title={link.head_oid ?? link.branch ?? undefined}
                >
                  {link.branch ??
                    (link.state === "detached"
                      ? t("Detached HEAD") +
                        (link.head_oid ? ` · ${link.head_oid.slice(0, 7)}` : "")
                      : link.state === "removed"
                        ? t("Removed")
                        : t("Status unavailable"))}
                </span>
              </div>
              {!link.implicit && (
                <Button
                  size="icon-sm"
                  variant="ghost"
                  aria-label={t("Unlink")}
                  title={t("Unlink")}
                  disabled={unlinkPending}
                  onClick={() => onUnlink(link.path)}
                >
                  <LinkSimpleBreak />
                </Button>
              )}
            </div>
            {link.state !== "removed" &&
              (actions?.openChanges ||
                actions?.openFiles ||
                actions?.openTerminal) && (
                <div className={styles.actions}>
                  {available &&
                    worktreeAction(
                      t("Changes"),
                      GitDiff,
                      actions.openChanges,
                      link.path,
                    )}
                  {worktreeAction(
                    t("Files"),
                    Folder,
                    actions.openFiles,
                    link.path,
                  )}
                  {worktreeAction(
                    t("Terminal"),
                    Terminal,
                    actions.openTerminal,
                    link.path,
                  )}
                </div>
              )}
          </li>
        );
      })}
    </ul>
  );
}

function UrlItems({
  items,
  unlinkPending,
  onUnlink,
}: {
  items: Schema<"ThreadLinksView">["urls"];
  unlinkPending: boolean;
  onUnlink: (url: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <ul className={styles.list}>
      {items.map((link) => {
        const host = new URL(link.url).hostname;
        const artifact = link.github;
        const repo = artifact ? githubRepoSlug(link.url) : null;
        const badge = artifact?.status && statusBadge[artifact.status];
        const name = artifact?.title || link.label || host;
        return (
          <li key={link.url} className={`${styles.row} ${styles.linkRow}`}>
            <UrlIcon artifact={artifact} className={styles.icon} />
            <a
              href={link.url}
              target="_blank"
              rel="noreferrer"
              className={styles.identity}
            >
              <span className={styles.name} title={link.url}>
                <span className={styles.nameText}>{name}</span>
                <ArrowSquareOut className={styles.externalIcon} aria-hidden />
              </span>
              {(artifact || name !== host) && (
                <span className={styles.detail}>
                  <span className={styles.detailText}>
                    {artifact ? (repo ?? host) : host}
                  </span>
                  {artifact && (
                    <span className={styles.artifactNumber}>
                      #{artifact.number}
                    </span>
                  )}
                  {badge && (
                    <Badge size="sm" variant={badge[1]}>
                      {t(badge[0])}
                    </Badge>
                  )}
                </span>
              )}
            </a>
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label={t("Unlink")}
              title={t("Unlink")}
              disabled={unlinkPending}
              onClick={() => onUnlink(link.url)}
            >
              <LinkSimpleBreak />
            </Button>
          </li>
        );
      })}
    </ul>
  );
}

export function ThreadLinksPreview() {
  const { t } = useTranslation();
  const bg = useBackground();
  const { threadLinks } = bg;
  const unlink = useUnlinkThreadResource(bg.threadId);
  if (!threadLinks.enabled) return null;
  const sections: readonly LinkSection[] = ["worktrees", "links"];
  return (
    <>
      {unlink.error && <ErrorNotice inline error={unlink.error} />}
      {sections.map((section) => {
        const target = sectionTarget(section, bg.threadId, t);
        const worktrees = threadLinks.worktrees.slice(0, 3);
        const urls = threadLinks.urls.slice(0, 3);
        const hasItems =
          section === "worktrees" ? worktrees.length > 0 : urls.length > 0;
        return (
          <section key={section} aria-label={target.title}>
            <div className={backgroundStyles.groupHeader}>
              <h3>{target.title}</h3>
              <Button
                variant="ghost"
                size="sm"
                aria-label={t("View all") + " · " + target.title}
                onClick={() => bg.open(target)}
              >
                {t("View all")}
              </Button>
            </div>
            {hasItems ? (
              section === "worktrees" ? (
                <WorktreeItems
                  items={worktrees}
                  actions={threadLinks.actions}
                  unlinkPending={unlink.isPending}
                  onUnlink={(path) =>
                    unlink.mutate({ kind: "worktree", target: path })
                  }
                />
              ) : (
                <UrlItems
                  items={urls}
                  unlinkPending={unlink.isPending}
                  onUnlink={(url) =>
                    unlink.mutate({ kind: "url", target: url })
                  }
                />
              )
            ) : (
              <p className={backgroundStyles.previewEmpty}>
                {threadLinks.isPending
                  ? t(
                      section === "worktrees"
                        ? "Loading worktrees"
                        : "Loading links",
                    )
                  : threadLinks.error
                    ? t(
                        section === "worktrees"
                          ? "Worktrees unavailable."
                          : "Links unavailable.",
                      )
                    : t(
                        section === "worktrees"
                          ? "No worktrees yet."
                          : "No links yet.",
                      )}
              </p>
            )}
          </section>
        );
      })}
    </>
  );
}

export function ThreadLinksPanel({
  threadId,
  section,
}: {
  threadId: string;
  section: LinkSection;
}) {
  const { t } = useTranslation();
  const bg = useBackground();
  const { threadLinks } = bg;
  const unlink = useUnlinkThreadResource(threadId);
  const Icon = section === "worktrees" ? GitBranch : LinkSimple;
  const label = t(SECTION_LABELS[section]);
  const itemsCount =
    section === "worktrees"
      ? threadLinks.worktrees.length
      : threadLinks.urls.length;
  return (
    <section className={backgroundStyles.panel} aria-label={label}>
      <header className={backgroundStyles.panelHeader}>
        <Icon size={18} />
        <div className={backgroundStyles.panelIdentity}>
          <h2>{label}</h2>
        </div>
        <ViewLink
          getLink={() =>
            new URL(
              "/threads/" + encodeURIComponent(threadId) + "?native=" + section,
              window.location.origin,
            ).href
          }
        />
      </header>
      <ScrollArea className={backgroundStyles.workBody} scrollFade>
        <ErrorNotice error={threadLinks.error} retry={threadLinks.refetch} />
        {unlink.error && <ErrorNotice inline error={unlink.error} />}
        {threadLinks.isPending ? (
          <p role="status">
            {t(section === "worktrees" ? "Loading worktrees" : "Loading links")}
          </p>
        ) : !threadLinks.enabled ? (
          <p className={styles.empty}>
            {t(
              section === "worktrees"
                ? "Worktrees unavailable."
                : "Links unavailable.",
            )}
          </p>
        ) : itemsCount === 0 && !threadLinks.error ? (
          <p className={styles.empty}>
            {t(section === "worktrees" ? "No worktrees yet." : "No links yet.")}
          </p>
        ) : section === "worktrees" ? (
          <WorktreeItems
            items={threadLinks.worktrees}
            actions={threadLinks.actions}
            unlinkPending={unlink.isPending}
            onUnlink={(path) =>
              unlink.mutate({ kind: "worktree", target: path })
            }
          />
        ) : (
          <UrlItems
            items={threadLinks.urls}
            unlinkPending={unlink.isPending}
            onUnlink={(url) => unlink.mutate({ kind: "url", target: url })}
          />
        )}
      </ScrollArea>
    </section>
  );
}

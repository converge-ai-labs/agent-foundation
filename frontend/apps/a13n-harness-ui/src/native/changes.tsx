import { useTranslation } from "../i18n";
import type { TFunction } from "i18next";
import { useEffect, useMemo, useState } from "react";
import {
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  Button,
  Menu,
  MenuTrigger,
  MenuPopup,
  MenuCheckboxItem,
} from "a13n-ui";
import {
  CaretRight,
  GitBranch,
  DotsThree,
  ArrowLeft,
  ArrowRight,
} from "@phosphor-icons/react";
import { result, type Schema } from "../transport/client";
import { useStatus, useTransport } from "../transport/context";
import { ErrorNotice, SearchField } from "../shell/ui";
import { DebugOnly } from "../shell/debug";
import { PatchEditor } from "./patch-editor";
import { gitPath } from "./buffer";
import styles from "./native.module.css";

type Page = { offset: number; expected_revision?: string };
export function gitChangeLabel(change: Schema<"GitChange">, t: TFunction) {
  if (change.kind === "conflicted") return t("Conflict");
  if (change.kind === "untracked") return t("New");
  if (change.kind === "ignored") return t("Ignored");
  const labels: Record<string, string> = {
    M: t("Modified"),
    A: t("Added"),
    D: t("Deleted"),
    R: t("Renamed"),
    C: t("Copied"),
    T: t("Type changed"),
  };
  return (
    [
      ...new Set(
        [change.index_status, change.worktree_status]
          .map((status) => labels[status])
          .filter(Boolean),
      ),
    ].join(" - ") || t("Changed")
  );
}
export function useGitStatus(path: string, includeIgnored: boolean) {
  const { client } = useTransport();
  const feature = useStatus().data?.features?.host_git;
  const discovery = useQuery({
    queryKey: ["native", "repository", path],
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/host/git/repository", {
          params: { query: { path } },
          signal,
        }),
      ),
    enabled: !!path && !!feature,
    refetchOnMount: "always",
  });
  const root = discovery.data?.repository?.root;
  const status = useInfiniteQuery({
    queryKey: ["native", "git-status", root, includeIgnored],
    initialPageParam: { offset: 0 } as Page,
    queryFn: ({ pageParam, signal }) =>
      result(
        client.GET("/api/host/git/status", {
          params: {
            query: {
              path: root!,
              include_ignored: includeIgnored,
              ...pageParam,
            },
          },
          signal,
        }),
      ),
    getNextPageParam: (last): Page | undefined =>
      last.next_offset === null
        ? undefined
        : { offset: last.next_offset, expected_revision: last.revision },
    enabled: !!root && discovery.data?.state === "repository" && !!feature,
    refetchOnMount: "always",
  });
  return {
    discovery,
    status,
    feature,
    entries: status.data?.pages.flatMap((page) => page.entries) ?? [],
  };
}
export function changeAxes(
  change: Schema<"GitChange">,
): Schema<"GitDiff">["comparison"][] {
  if (change.kind === "ignored") return [];
  if (change.kind === "untracked") return ["untracked"];
  const axes: Schema<"GitDiff">["comparison"][] = [];
  if (change.index_status !== "." && change.index_status !== " ")
    axes.push("staged");
  if (change.worktree_status !== "." && change.worktree_status !== " ")
    axes.push("unstaged");
  return axes;
}
export type DiffSelection = {
  repository_path: string;
  path: string;
  comparison: Schema<"GitDiff">["comparison"];
};
export function Changes({
  path,
  openFile,
  selected,
  select,
  onChoices,
}: {
  path: string;
  openFile: (path: string) => void;
  selected: DiffSelection | null;
  select: (selection: DiffSelection | null, choices?: DiffSelection[]) => void;
  onChoices?: (choices: DiffSelection[]) => void;
}) {
  const { t } = useTranslation();
  const queries = useQueryClient();
  const [ignored, setIgnored] = useState(false);
  const [filter, setFilter] = useState("");
  const { discovery, status, feature, entries } = useGitStatus(path, ignored);
  const repository =
    status.data?.pages[0]?.repository ?? discovery.data?.repository;
  const choices = useMemo(() => {
    const groups = ["staged", "unstaged", "untracked"] as const;
    const matches = (
      status.data?.pages.flatMap((page) => page.entries) ?? []
    ).filter((entry) =>
      `${entry.path} ${entry.original_path ?? ""}`
        .toLowerCase()
        .includes(filter.toLowerCase()),
    );
    return repository
      ? groups.flatMap((comparison) =>
          matches
            .filter((entry) => changeAxes(entry).includes(comparison))
            .map((entry) => ({
              repository_path: repository.root,
              path: entry.path,
              comparison,
            })),
        )
      : [];
  }, [status.data, repository?.root, filter]);
  useEffect(() => {
    onChoices?.(choices);
  }, [choices, onChoices]);
  if (!feature)
    return (
      <div className={styles.empty}>
        <h3>{t("Git unavailable")}</h3>
        <p>
          {t(
            "Install Git on the server and turn on computer sharing to view changes.",
          )}
        </p>
      </div>
    );
  if (!path)
    return <p>{t("Choose a server directory to inspect its repository.")}</p>;
  const groups = ["staged", "unstaged", "untracked"] as const;
  const matches = entries.filter((entry) =>
    `${entry.path} ${entry.original_path ?? ""}`
      .toLowerCase()
      .includes(filter.toLowerCase()),
  );
  return (
    <div className={styles.changes}>
      <ErrorNotice
        error={discovery.error || status.error}
        retry={() => {
          void queries.resetQueries({ queryKey: ["native", "git-status"] });
          void discovery.refetch();
        }}
      />
      {(discovery.isFetching || status.isFetching) && (
        <small role="status">{t("Refreshing Git status…")}</small>
      )}
      {discovery.data?.state !== "repository" && discovery.data && (
        <div className={styles.empty}>
          <h3>
            {discovery.data.state === "bare"
              ? t("Bare repository")
              : t("No Git worktree here")}
          </h3>
          <p>
            {t(
              "Choose a Git worktree folder. Other files are still available in Files.",
            )}
          </p>
        </div>
      )}
      {repository && (
        <>
          <details className={styles.repo}>
            <summary
              aria-label={t("Repository details")}
              title={repository.root}
            >
              <GitBranch size={14} aria-hidden />
              <span>
                {repository.branch ?? t("Detached HEAD")}
                {!repository.head_oid ? t(" - No commits yet") : ""}
              </span>
              <CaretRight size={12} aria-hidden />
            </summary>
            <div>
              <p>{t("Worktree: {{path}}", { path: repository.root })}</p>
              <DebugOnly>
                <p>
                  {t("HEAD: {{head}}", {
                    head: repository.head_oid ?? t("Empty tree (no commits)"),
                  })}
                </p>
                <p>
                  {t("Git directory: {{path}}", { path: repository.git_dir })}
                </p>
                <p>
                  {t("Common directory: {{path}}", {
                    path: repository.common_dir,
                  })}
                </p>
              </DebugOnly>
              <p>{t("Changes include all work in this checkout.")}</p>
            </div>
          </details>
          <div className={styles.directoryToolbar}>
            <SearchField
              label={t("Filter loaded changes by path")}
              placeholder={t("Filter changes…")}
              value={filter}
              onChange={setFilter}
            />
            <Menu>
              <MenuTrigger
                render={
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={t("Change filters")}
                  />
                }
              >
                <DotsThree />
              </MenuTrigger>
              <MenuPopup align="end">
                <MenuCheckboxItem
                  checked={ignored}
                  onCheckedChange={setIgnored}
                >
                  {t("Show ignored entries")}
                </MenuCheckboxItem>
              </MenuPopup>
            </Menu>
          </div>
          {status.hasNextPage && (
            <small>
              {t("Counts and filtering cover loaded changes only.")}
            </small>
          )}
          {groups.map((axis) => {
            const rows = matches.filter((entry) =>
              changeAxes(entry).includes(axis),
            );
            if (!rows.length) return null;
            return (
              <details className={styles.changeGroup} key={axis} open>
                <summary
                  title={
                    axis === "staged"
                      ? t("HEAD → index")
                      : axis === "unstaged"
                        ? t("Index → worktree")
                        : t("New files")
                  }
                >
                  <CaretRight size={12} aria-hidden />
                  <span>
                    {axis === "staged"
                      ? t("Staged")
                      : axis === "unstaged"
                        ? t("Unstaged")
                        : t("Untracked")}
                  </span>
                  <small>
                    {rows.length}
                    {status.hasNextPage ? "+" : ""}
                  </small>
                </summary>
                {rows.map((entry) => (
                  <button
                    type="button"
                    className={`${styles.changeRow} ${selected?.path === entry.path && selected.comparison === axis && selected.repository_path === repository.root ? styles.selected : ""}`}
                    key={entry.path}
                    title={entry.path}
                    onClick={() =>
                      select(
                        {
                          repository_path: repository.root,
                          path: entry.path,
                          comparison: axis,
                        },
                        choices,
                      )
                    }
                  >
                    <span>
                      {entry.original_path ? `${entry.original_path} → ` : ""}
                      {entry.path}
                    </span>
                    <small>
                      {entry.kind === "conflicted"
                        ? t("Conflict")
                        : axis === "staged"
                          ? entry.index_status
                          : axis === "unstaged"
                            ? entry.worktree_status
                            : t("New")}
                      {entry.submodule && entry.submodule !== "N..."
                        ? ` - ${entry.submodule}`
                        : ""}
                    </small>
                  </button>
                ))}
              </details>
            );
          })}
          {ignored &&
            matches
              .filter((entry) => entry.kind === "ignored")
              .map((entry) => (
                <button
                  type="button"
                  className={styles.changeRow}
                  key={entry.path}
                  onClick={() => openFile(gitPath(repository.root, entry.path))}
                >
                  <span>{entry.path}</span>
                  <small>{t("Open in Files")}</small>
                </button>
              ))}
          {!matches.length && status.isSuccess && !status.error && (
            <p className={styles.changeEmpty}>
              {filter
                ? t("No loaded changes match this filter.")
                : status.hasNextPage
                  ? t("No changes in this loaded page.")
                  : t("No changes in this worktree.")}
            </p>
          )}
          {status.hasNextPage && (
            <Button
              variant="ghost"
              size="sm"
              disabled={status.isFetching}
              onClick={() => void status.fetchNextPage()}
            >
              {t("Load more changes")}
            </Button>
          )}
        </>
      )}
    </div>
  );
}
export function DiffView({
  selection,
  threadId,
  openFile,
  choices = [],
  navigate,
}: {
  selection: DiffSelection;
  threadId?: string;
  openFile: (path: string, line?: number) => void;
  choices?: DiffSelection[];
  navigate?: (selection: DiffSelection) => void;
}) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const diff = useQuery({
    queryKey: ["native", "diff", selection],
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/host/git/diff", {
          params: { query: selection },
          signal,
        }),
      ),
    refetchOnMount: "always",
  });
  const value = diff.data;
  const index = choices.findIndex(
    (item) =>
      item.repository_path === selection.repository_path &&
      item.path === selection.path &&
      item.comparison === selection.comparison,
  );
  return (
    <section className={styles.file} aria-label={t("Reviewed Git diff")}>
      <div className={styles.fileHeader}>
        {navigate && choices.length > 1 && (
          <nav
            className={styles.changeNavigation}
            aria-label={t("Review changed files")}
          >
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label={t("Previous file")}
              title={t("Previous file")}
              disabled={index <= 0}
              onClick={() => navigate(choices[index - 1]!)}
            >
              <ArrowLeft />
            </Button>
            <span>{index >= 0 ? `${index + 1} / ${choices.length}` : "—"}</span>
            <Button
              size="icon-sm"
              variant="ghost"
              aria-label={t("Next file")}
              title={t("Next file")}
              disabled={index < 0 || index >= choices.length - 1}
              onClick={() => navigate(choices[index + 1]!)}
            >
              <ArrowRight />
            </Button>
          </nav>
        )}
        <Button
          variant="ghost"
          size="sm"
          onClick={() =>
            openFile(gitPath(selection.repository_path, selection.path))
          }
        >
          {t("Open in Files")}
        </Button>
      </div>
      <ErrorNotice error={diff.error} retry={() => void diff.refetch()} />
      {diff.isFetching && <p role="status">{t("Reading comparison…")}</p>}
      {value && (
        <>
          {value.original_path && (
            <p>
              {t("Rename: {{from}} → {{to}}", {
                from: value.original_path,
                to: value.path,
              })}
            </p>
          )}
          {value.presentation === "text" && value.text != null ? (
            <PatchEditor
              key={value.revision}
              value={value}
              threadId={threadId}
              disabled={diff.isFetching || !!diff.error}
              openLine={
                selection.comparison === "unstaged" ||
                selection.comparison === "untracked"
                  ? (line) =>
                      openFile(
                        gitPath(selection.repository_path, selection.path),
                        line,
                      )
                  : undefined
              }
            />
          ) : (
            <p>
              {value.presentation === "binary"
                ? t(
                    "This isn't UTF-8 text. Open it in Files to attach the file instead.",
                  )
                : t("No changes on this comparison axis.")}
            </p>
          )}
          <DebugOnly>
            <details className={styles.metadata}>
              <summary>{t("Comparison version")}</summary>
              <p>
                {t("Repository: {{path}}", { path: value.repository.root })}
              </p>
              <p>
                {t("HEAD: {{head}}", {
                  head:
                    value.repository.head_oid ?? t("Empty tree (unborn HEAD)"),
                })}
              </p>
              <p>
                {t("Index: {{revision}}", { revision: value.index_revision })}
              </p>
              <p>
                {t("Diff revision: {{revision}}", { revision: value.revision })}
              </p>
              <p>
                {t("Git directory: {{path}}", {
                  path: value.repository.git_dir,
                })}
              </p>
            </details>
          </DebugOnly>
        </>
      )}
    </section>
  );
}

import { useEffect, useMemo, useState } from "react";
import {
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { result, type Schema } from "../transport/client";
import { useStatus, useTransport } from "../transport/context";
import { ErrorNotice, TextField } from "../shell/ui";
import { PatchEditor } from "./patch-editor";
import { gitPath } from "./buffer";
import styles from "./native.module.css";

type Page = { offset: number; expected_revision?: string };
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
        <h3>Git unavailable</h3>
        <p>
          Git must be installed on the server and native sharing enabled. Files
          remains independent; this is not a clean-tree result.
        </p>
      </div>
    );
  if (!path) return <p>Choose a native directory to inspect its repository.</p>;
  const groups = ["staged", "unstaged", "untracked"] as const;
  const matches = entries.filter((entry) =>
    `${entry.path} ${entry.original_path ?? ""}`
      .toLowerCase()
      .includes(filter.toLowerCase()),
  );
  return (
    <div className={styles.stack}>
      <ErrorNotice
        error={discovery.error || status.error}
        retry={() => {
          void queries.resetQueries({ queryKey: ["native", "git-status"] });
          void discovery.refetch();
        }}
      />
      {(discovery.isFetching || status.isFetching) && (
        <small role="status">Refreshing Git observations…</small>
      )}
      {discovery.data?.state !== "repository" && discovery.data && (
        <div className={styles.empty}>
          <h3>
            {discovery.data.state === "bare"
              ? "Bare repository"
              : "No Git worktree here"}
          </h3>
          <p>
            Choose a worktree directory. Ordinary native files remain
            accessible.
          </p>
        </div>
      )}
      {repository && (
        <>
          <div className={styles.repo}>
            <strong>
              {repository.branch ?? "Detached HEAD"}
              {!repository.head_oid ? " · Unborn HEAD" : ""}
            </strong>
            <div className={styles.path}>{repository.root}</div>
            <details>
              <summary>Repository identity</summary>
              <p>HEAD: {repository.head_oid ?? "Empty tree (no commits)"}</p>
              <p>Git directory: {repository.git_dir}</p>
              <p>Common directory: {repository.common_dir}</p>
              <p>
                This is shared checkout state, not changes attributed to a Run.
              </p>
            </details>
          </div>
          <label className={styles.check}>
            <input
              type="checkbox"
              checked={ignored}
              onChange={(event) => setIgnored(event.target.checked)}
            />
            Show ignored entries
          </label>
          <TextField
            label="Filter loaded changes by path"
            value={filter}
            onChange={setFilter}
          />
          {status.hasNextPage && (
            <small>Counts and filtering cover loaded changes only.</small>
          )}
          {groups.map((axis) => {
            const rows = matches.filter((entry) =>
              changeAxes(entry).includes(axis),
            );
            return (
              <details className={styles.changeGroup} key={axis} open>
                <summary>
                  {axis === "staged"
                    ? "Staged · HEAD → index"
                    : axis === "unstaged"
                      ? "Unstaged · index → worktree"
                      : "Untracked · new files"}{" "}
                  <span>
                    {rows.length}
                    {status.hasNextPage ? "+" : ""}
                  </span>
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
                        ? "Conflict"
                        : axis === "staged"
                          ? entry.index_status
                          : axis === "unstaged"
                            ? entry.worktree_status
                            : "New"}
                      {entry.submodule && entry.submodule !== "N..."
                        ? ` · ${entry.submodule}`
                        : ""}
                    </small>
                  </button>
                ))}
                {!rows.length && status.isSuccess && !status.error && (
                  <small>
                    No {axis} entries{status.hasNextPage ? " in this page" : ""}
                    .
                  </small>
                )}
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
                  <small>Ignored · open in Files</small>
                </button>
              ))}
          {status.hasNextPage && (
            <Button
              variant="ghost"
              size="sm"
              disabled={status.isFetching}
              onClick={() => void status.fetchNextPage()}
            >
              Load more changes
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
    <section className={styles.file} aria-label="Reviewed Git diff">
      <div className={styles.fileHeader}>
        <strong>{selection.path}</strong>
        <Button
          variant="ghost"
          size="sm"
          onClick={() =>
            openFile(gitPath(selection.repository_path, selection.path))
          }
        >
          Open in Files
        </Button>
      </div>
      {navigate && choices.length > 1 && (
        <nav className={styles.actions} aria-label="Review changed files">
          <Button
            size="sm"
            variant="ghost"
            disabled={index <= 0}
            onClick={() => navigate(choices[index - 1]!)}
          >
            Previous file
          </Button>
          <small>
            {index >= 0
              ? `${index + 1} / ${choices.length} loaded comparisons`
              : "Comparison no longer in the list"}
          </small>
          <Button
            size="sm"
            variant="ghost"
            disabled={index < 0 || index >= choices.length - 1}
            onClick={() => navigate(choices[index + 1]!)}
          >
            Next file
          </Button>
        </nav>
      )}
      <ErrorNotice error={diff.error} retry={() => void diff.refetch()} />
      {diff.isFetching && <p role="status">Reading comparison…</p>}
      {value && (
        <>
          {value.original_path && (
            <p>
              Rename: {value.original_path} → {value.path}
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
                ? "Binary or non-UTF-8 comparison. Open in Files to deliberately capture file bytes instead."
                : "No changes on this comparison axis."}
            </p>
          )}
          <details className={styles.metadata}>
            <summary>Reviewed comparison identity</summary>
            <p>Repository: {value.repository.root}</p>
            <p>
              HEAD: {value.repository.head_oid ?? "Empty tree (unborn HEAD)"}
            </p>
            <p>Index: {value.index_revision}</p>
            <p>Diff revision: {value.revision}</p>
            <p>Git directory: {value.repository.git_dir}</p>
          </details>
        </>
      )}
    </section>
  );
}

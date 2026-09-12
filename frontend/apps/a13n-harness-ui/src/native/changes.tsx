import { useState } from "react";
import {
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { result, type Schema } from "../transport/client";
import { useStatus, useTransport } from "../transport/context";
import { ErrorNotice } from "../shell/ui";
import { SourceEditor } from "../configuration/editor";
import { CaptureContext } from "./capture";
import { gitPath, type LineRange } from "./buffer";
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
  threadId,
  openFile,
  selected,
  select,
}: {
  path: string;
  threadId?: string;
  openFile: (path: string) => void;
  selected: DiffSelection | null;
  select: (selection: DiffSelection | null) => void;
}) {
  const queries = useQueryClient();
  const [ignored, setIgnored] = useState(false);
  const { discovery, status, feature, entries } = useGitStatus(path, ignored);
  const repository =
    status.data?.pages[0]?.repository ?? discovery.data?.repository;
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
          {groups.map((axis) => {
            const rows = entries.filter((entry) =>
              changeAxes(entry).includes(axis),
            );
            return (
              <section className={styles.changeGroup} key={axis}>
                <h3>
                  {axis === "staged"
                    ? "Staged · HEAD → index"
                    : axis === "unstaged"
                      ? "Unstaged · index → worktree"
                      : "Untracked · new files"}{" "}
                  <span>
                    {rows.length}
                    {status.hasNextPage ? "+" : ""}
                  </span>
                </h3>
                {rows.map((entry) => (
                  <button
                    type="button"
                    className={`${styles.changeRow} ${selected?.path === entry.path && selected.comparison === axis && selected.repository_path === repository.root ? styles.selected : ""}`}
                    key={entry.path}
                    onClick={() =>
                      select({
                        repository_path: repository.root,
                        path: entry.path,
                        comparison: axis,
                      })
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
              </section>
            );
          })}
          {ignored &&
            entries
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
      {selected && (
        <DiffView
          key={`${selected.repository_path}:${selected.path}:${selected.comparison}`}
          selection={selected}
          threadId={threadId}
          openFile={openFile}
        />
      )}
    </div>
  );
}
function DiffView({
  selection,
  threadId,
  openFile,
}: {
  selection: DiffSelection;
  threadId?: string;
  openFile: (path: string) => void;
}) {
  const { client } = useTransport();
  const [range, setRange] = useState<LineRange>();
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
      <ErrorNotice error={diff.error} retry={() => void diff.refetch()} />
      {diff.isFetching && <p role="status">Reading comparison…</p>}
      {value && (
        <>
          <p>
            {value.comparison === "staged"
              ? "HEAD → index"
              : value.comparison === "unstaged"
                ? "Index → worktree"
                : "New-file comparison"}{" "}
            · {value.repository.root}
          </p>
          {value.original_path && (
            <p>
              Rename: {value.original_path} → {value.path}
            </p>
          )}
          {value.presentation === "text" && value.text != null ? (
            <>
              <SourceEditor
                value={value.text}
                language="plain"
                readOnly
                label="Git patch (line numbers include headers)"
                onSelection={setRange}
              />
              <CaptureContext
                key={value.revision}
                source={{ diff: value }}
                threadId={threadId}
                selection={range}
                disabled={diff.isFetching || !!diff.error}
              />
            </>
          ) : (
            <p>
              {value.presentation === "binary"
                ? "Binary or non-UTF-8 comparison. Open in Files to deliberately capture file bytes instead."
                : "No changes on this comparison axis."}
            </p>
          )}
          <details className={styles.metadata}>
            <summary>Reviewed comparison identity</summary>
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

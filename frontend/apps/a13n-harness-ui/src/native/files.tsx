import { useContext, useState } from "react";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import {
  Folder,
  File,
  Link as LinkIcon,
  DotsThree,
  CaretRight,
} from "@phosphor-icons/react";
import { result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import { ErrorNotice, TextField } from "../shell/ui";
import { basename, joinPath, gitPath, FileBuffers, withinRoot } from "./buffer";
import { useGitStatus } from "./changes";
import styles from "./native.module.css";

type Operation =
  | { kind: "new-file" | "new-directory" | "upload" }
  | { kind: "move" | "delete" | "replace"; entry: Schema<"FileEntry"> };
type DirectoryCursor = { offset: number; revision?: string };
export function Files({
  directory,
  path,
  open,
  refresh,
  roots,
  openTerminal,
}: {
  directory: string;
  roots?: string[];
  openTerminal?: (directory: string) => void;
  path: string;
  open: (path: string) => void;
  refresh: () => void;
}) {
  const { client } = useTransport();
  const queries = useQueryClient();
  const buffers = useContext(FileBuffers);
  const [operation, setOperation] = useState<Operation | null>(null);
  const [hideIgnored, setHideIgnored] = useState(false);
  const [filter, setFilter] = useState("");
  const [copyError, setCopyError] = useState(false);
  const copyPath = (path: string) => {
    void navigator.clipboard.writeText(path).then(
      () => setCopyError(false),
      () => setCopyError(true),
    );
  };
  const git = useGitStatus(directory, true);
  const listing = useInfiniteQuery({
    queryKey: ["native", "directory", directory],
    initialPageParam: { offset: 0 } as DirectoryCursor,
    queryFn: ({ pageParam, signal }) =>
      result(
        client.GET("/api/host/files", {
          params: { query: { path: directory, ...pageParam } },
          signal,
        }),
      ),
    getNextPageParam: (last): DirectoryCursor | undefined =>
      last.next_offset === null
        ? undefined
        : { offset: last.next_offset, revision: last.directory.revision },
    enabled: !!directory,
    refetchOnMount: "always",
  });
  const entries = listing.data?.pages.flatMap((page) => page.entries) ?? [];
  const repo = git.status.data?.pages[0]?.repository;
  const dirty = [...buffers.entries()].filter(
    ([path, buffer]) =>
      (!roots || roots.some((root) => withinRoot(path, root))) &&
      (buffer.dirty || buffer.uncertain || buffer.saving),
  );
  return (
    <div className={styles.stack}>
      {!!dirty.length && (
        <div className={styles.buffers} aria-label="Local unsaved files">
          <small>Private buffers · retained in this tab</small>
          {dirty.map(([name]) => (
            <Button
              key={name}
              variant="ghost"
              size="sm"
              onClick={() => open(name)}
            >
              {basename(name)} · Unsaved
            </Button>
          ))}
        </div>
      )}
      {directory && (
        <>
          <div className={styles.actions}>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setOperation({ kind: "new-file" })}
            >
              New file
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setOperation({ kind: "new-directory" })}
            >
              New folder
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setOperation({ kind: "upload" })}
            >
              Upload
            </Button>
            {git.feature && (
              <label className={styles.check}>
                <input
                  type="checkbox"
                  checked={hideIgnored}
                  onChange={(event) => setHideIgnored(event.target.checked)}
                />
                Hide Git-ignored entries
              </label>
            )}
          </div>
          <TextField
            label="Filter loaded files in this directory"
            value={filter}
            onChange={setFilter}
          />
          {listing.hasNextPage && (
            <small>
              Filtering loaded entries only. Load more files to include the rest
              of this directory.
            </small>
          )}
          {copyError && (
            <small role="status">
              Copy unavailable. Select the file path to copy it.
            </small>
          )}
          <details className={styles.entryActions}>
            <summary>Folder actions</summary>
            <div>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => copyPath(directory)}
              >
                Copy folder path
              </Button>
              {openTerminal && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => openTerminal(directory)}
                >
                  Open terminal here
                </Button>
              )}
            </div>
          </details>
          <ErrorNotice
            error={listing.error}
            retry={() =>
              void queries.resetQueries({
                queryKey: ["native", "directory", directory],
              })
            }
          />
          {listing.isFetching && (
            <small role="status">Reading directory…</small>
          )}
          {git.feature && (git.discovery.error || git.status.error) && (
            <small>
              Git status unavailable. Files are still shown without status
              filtering.
            </small>
          )}
          {git.status.hasNextPage && (
            <small>
              Git badges cover loaded status entries only. Open Changes to
              inspect further pages.
            </small>
          )}
          {listing.data?.pages[0]?.resolved_path !== directory &&
            listing.data && (
              <small>
                Resolved directory: {listing.data.pages[0]!.resolved_path}
              </small>
            )}
          <div className={styles.fileList} aria-label="Directory entries">
            {entries
              .filter((entry) =>
                basename(entry.path)
                  .toLowerCase()
                  .includes(filter.toLowerCase()),
              )
              .map((entry) => {
                const change = repo
                  ? git.entries.find(
                      (item) =>
                        gitPath(repo.root, item.path.replace(/\/$/, "")) ===
                        entry.path,
                    )
                  : undefined;
                if (
                  hideIgnored &&
                  !git.status.error &&
                  change?.kind === "ignored"
                )
                  return null;
                const Icon =
                  entry.kind === "directory"
                    ? Folder
                    : entry.kind === "symlink"
                      ? LinkIcon
                      : File;
                return (
                  <div
                    key={entry.path}
                    className={`${styles.fileRow} ${entry.path === path ? styles.selected : ""}`}
                  >
                    <button
                      type="button"
                      className={styles.fileLink}
                      title={entry.path}
                      onClick={() => open(entry.path)}
                    >
                      <Icon size={16} />
                      <span>{basename(entry.path)}</span>
                      <small>
                        {change
                          ? `${change.index_status}${change.worktree_status}`
                          : entry.kind === "file"
                            ? `${entry.size.toLocaleString()} B`
                            : entry.kind}
                      </small>
                      {entry.kind === "directory" && (
                        <CaretRight size={14} aria-hidden="true" />
                      )}
                    </button>
                    <details className={styles.entryActions}>
                      <summary
                        aria-label={`Actions for ${basename(entry.path)}`}
                      >
                        <DotsThree size={20} />
                      </summary>
                      <div>
                        <small>
                          {entry.kind}
                          {entry.link_target ? ` → ${entry.link_target}` : ""}
                        </small>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => copyPath(entry.path)}
                        >
                          Copy path
                        </Button>
                        {openTerminal && entry.kind === "directory" && (
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() => openTerminal(entry.path)}
                          >
                            Open terminal here
                          </Button>
                        )}
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => setOperation({ kind: "move", entry })}
                        >
                          Rename or move
                        </Button>
                        {entry.kind === "file" && (
                          <Button
                            size="sm"
                            variant="ghost"
                            onClick={() =>
                              setOperation({ kind: "replace", entry })
                            }
                          >
                            Replace from upload
                          </Button>
                        )}
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() =>
                            setOperation({ kind: "delete", entry })
                          }
                        >
                          Delete
                        </Button>
                      </div>
                    </details>
                  </div>
                );
              })}
            {filter &&
              entries.length > 0 &&
              !entries.some((entry) =>
                basename(entry.path)
                  .toLowerCase()
                  .includes(filter.toLowerCase()),
              ) && <p className={styles.empty}>No matching loaded files.</p>}
            {!entries.length && listing.isSuccess && !listing.error && (
              <p className={styles.empty}>This directory is empty.</p>
            )}
          </div>
          {listing.hasNextPage && (
            <Button
              variant="ghost"
              size="sm"
              disabled={listing.isFetching}
              onClick={() => void listing.fetchNextPage()}
            >
              Load more files
            </Button>
          )}
        </>
      )}
      <ModalFrame
        open={!!operation}
        onOpenChange={(value) => {
          if (!value) setOperation(null);
        }}
        title={
          operation?.kind === "delete"
            ? "Delete native entry"
            : operation?.kind === "move"
              ? "Rename or move native entry"
              : operation?.kind === "replace"
                ? "Replace native file from upload"
                : operation?.kind === "upload"
                  ? "Upload to server"
                  : operation?.kind === "new-directory"
                    ? "Create native folder"
                    : "Create native file"
        }
        description="This operates on the server's filesystem, not an Agent Environment."
        closeLabel="Close"
      >
        {operation && (
          <FileOperation
            key={
              operation.kind +
              ("entry" in operation ? operation.entry.path : "")
            }
            operation={operation}
            directory={directory}
            refresh={refresh}
            done={(target) => {
              setOperation(null);
              if (target) open(target);
            }}
          />
        )}
      </ModalFrame>
    </div>
  );
}
export function FileOperation({
  operation,
  directory,
  refresh,
  done,
}: {
  operation: Operation;
  directory: string;
  refresh: () => void;
  done: (target?: string) => void;
}) {
  const { client, fetch } = useTransport();
  const [destination, setDestination] = useState(
    "entry" in operation ? operation.entry.path : joinPath(directory, ""),
  );
  const [upload, setUpload] = useState<File | null>(null);
  const [recursive, setRecursive] = useState(false);
  const [pending, setPending] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const submit = async () => {
    if (pending || attempted) return;
    setPending(true);
    setError(null);
    setAttempted(true);
    try {
      if (operation.kind === "new-file")
        await result(
          client.PUT("/api/host/files/text", {
            body: { path: destination, text: "" },
          }),
        );
      else if (operation.kind === "new-directory")
        await result(
          client.POST("/api/host/files/directories", {
            body: { path: destination },
          }),
        );
      else if (operation.kind === "move")
        await result(
          client.POST("/api/host/files/move", {
            body: {
              path: operation.entry.path,
              destination,
              expected_revision: operation.entry.revision,
            },
          }),
        );
      else if (operation.kind === "delete")
        await result(
          client.POST("/api/host/files/delete", {
            body: {
              path: operation.entry.path,
              expected_revision: operation.entry.revision,
              recursive,
            },
          }),
        );
      else {
        if (!upload || upload.size > 10 * 1024 * 1024)
          throw new Error("Choose a file no larger than 10 MiB.");
        await fetch(
          `/api/host/files/content?${new URLSearchParams({ path: destination, ...(operation.kind === "replace" ? { expected_revision: operation.entry.revision } : {}) })}`,
          {
            method: "PUT",
            headers: { "Content-Type": "application/octet-stream" },
            body: upload,
          },
        );
      }
      done(operation.kind === "delete" ? undefined : destination);
    } catch (failure) {
      setError(failure);
    } finally {
      setPending(false);
      refresh();
    }
  };
  return (
    <form
      className={styles.stack}
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      {"entry" in operation && (
        <div className={styles.path}>
          {operation.entry.path}
          <small>Observed revision: {operation.entry.revision}</small>
        </div>
      )}
      {operation.kind === "delete" ? (
        <>
          <p>
            Delete this {operation.entry.kind}? This affects everyone using the
            server. There is no undo. Local unsaved buffers are retained
            separately.
          </p>
          {operation.entry.kind === "directory" && (
            <label className={styles.check}>
              <input
                type="checkbox"
                checked={recursive}
                onChange={(event) => setRecursive(event.target.checked)}
              />
              Recursively delete this folder and its contents
            </label>
          )}
        </>
      ) : operation.kind === "replace" ? (
        <p>
          Replace this file's bytes with the chosen upload? This affects the
          shared filesystem. Local unsaved text remains separate. The observed
          revision is required; an external change causes a conflict.
        </p>
      ) : (
        <TextField
          label="Absolute destination path"
          value={destination}
          onChange={setDestination}
        />
      )}
      {(operation.kind === "upload" || operation.kind === "replace") && (
        <label className={styles.stack}>
          Upload file (up to 10 MiB)
          <input
            type="file"
            onChange={(event) => {
              const file = event.target.files?.[0];
              setUpload(file ?? null);
              if (file && operation.kind === "upload")
                setDestination(joinPath(directory, file.name));
            }}
          />
        </label>
      )}
      {operation.kind !== "delete" && operation.kind !== "replace" && (
        <p>
          The destination must not exist. To replace a file, open its reviewed
          text and save with its observed revision, or choose a new upload name.
        </p>
      )}
      {operation.kind === "delete" && recursive && (
        <p>
          A failure can leave a partially removed tree. Refresh before any
          further action.
        </p>
      )}
      <ErrorNotice error={error} />
      {error != null && (
        <p>
          Close this dialog and inspect refreshed paths before choosing another
          action. An interrupted response does not undo a native operation.
        </p>
      )}
      <Button
        type="submit"
        loading={pending}
        disabled={
          attempted ||
          !destination ||
          ((operation.kind === "upload" || operation.kind === "replace") &&
            (!upload || upload.size > 10 * 1024 * 1024))
        }
      >
        {operation.kind === "delete"
          ? "Confirm delete"
          : operation.kind === "move"
            ? "Move entry"
            : operation.kind === "replace"
              ? "Confirm replacement"
              : operation.kind === "upload"
                ? "Upload file"
                : "Create"}
      </Button>
    </form>
  );
}

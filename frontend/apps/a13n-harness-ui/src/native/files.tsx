import { useTranslation } from "../i18n";
import {
  Fragment,
  type ReactNode,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";
import {
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import {
  Button,
  Checkbox,
  ModalFrame,
  Menu,
  MenuTrigger,
  MenuPopup,
  ContextMenu,
  ContextMenuTrigger,
  MenuItem,
  MenuCheckboxItem,
  MenuSeparator,
  MenuGroup,
  MenuGroupLabel,
  FileTypeIcon,
} from "a13n-ui";
import {
  FolderOpen,
  UploadSimple,
  DownloadSimple,
  Copy,
  TerminalWindow,
  PencilSimple,
  Trash,
  DotsThree,
} from "@phosphor-icons/react";
import { result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import { ErrorNotice, SearchField, TextField } from "../shell/ui";
import layout from "../shell/workbench.module.css";
import { DebugId } from "../shell/debug";
import {
  basename,
  parentPath,
  separator,
  joinPath,
  gitPath,
  FileBuffers,
  withinRoot,
} from "./buffer";
import { gitChangeLabel, useGitStatus } from "./changes";
import styles from "./native.module.css";
import { unavailablePreview } from "./document-kind";
import { downloadFile, fileTransfer } from "./file-transfer";
import { copyText } from "../platform";

import type { FileListPosition } from "./workspace-tabs";

type Operation = {
  kind: "move" | "delete" | "replace";
  entry: Schema<"FileEntry">;
};
type DirectoryCursor = { offset: number; revision?: string };
function useDirectoryListing(directory: string, target?: string) {
  const { client } = useTransport();
  const listing = useInfiniteQuery({
    queryKey: ["native", "directory", directory],
    initialPageParam: { offset: 0 } as DirectoryCursor,
    placeholderData: keepPreviousData,
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
  const entries = (
    listing.data?.pages.flatMap((page) => page.entries) ?? []
  ).sort(
    (a, b) =>
      Number(b.kind === "directory") - Number(a.kind === "directory") ||
      basename(a.path).localeCompare(basename(b.path), undefined, {
        numeric: true,
        sensitivity: "base",
      }),
  );
  const missingBranch =
    !!target &&
    withinRoot(target, directory) &&
    !entries.some((entry) => withinRoot(target, entry.path));
  const { hasNextPage, isFetching, isError, isPlaceholderData, fetchNextPage } =
    listing;
  useEffect(() => {
    if (
      missingBranch &&
      hasNextPage &&
      !isFetching &&
      !isError &&
      !isPlaceholderData
    )
      void fetchNextPage();
  }, [
    missingBranch,
    hasNextPage,
    isFetching,
    isError,
    isPlaceholderData,
    fetchNextPage,
  ]);
  return { listing, entries };
}
type RenderEntry = (entry: Schema<"FileEntry">) => ReactNode;

function DirectoryChildren({
  directory,
  renderEntry,
  target,
}: {
  directory: string;
  target?: string;
  renderEntry: RenderEntry;
}) {
  const { t } = useTranslation();
  const { listing, entries } = useDirectoryListing(directory, target);
  const queries = useQueryClient();
  return (
    <div
      className={styles.directoryChildren}
      role="group"
      aria-label={basename(directory)}
      aria-busy={listing.isFetching}
    >
      {listing.isPending && (
        <small role="status" className={styles.directoryLoading}>
          {t("Reading directory…")}
        </small>
      )}
      <ErrorNotice
        error={listing.error}
        retry={() =>
          void queries.resetQueries({
            queryKey: ["native", "directory", directory],
            exact: true,
          })
        }
      />
      {entries.map(renderEntry)}
      {listing.isSuccess && !entries.length && (
        <small className={styles.empty}>{t("This directory is empty.")}</small>
      )}
      {listing.hasNextPage && (
        <Button
          size="sm"
          variant="ghost"
          disabled={listing.isFetching}
          onClick={() => void listing.fetchNextPage()}
        >
          {t("Load more files")}
        </Button>
      )}
    </div>
  );
}
export function Files({
  directory,
  path,
  open,
  refresh,
  roots,
  openTerminal,
  fileManager,
  position,
}: {
  position?: FileListPosition;
  fileManager?: Schema<"ListenerFeatures">["file_manager"];
  directory: string;
  roots?: string[];
  openTerminal?: (directory: string) => void;
  path: string;
  open: (
    path: string,
    kind?: Schema<"FileEntry">["kind"],
    position?: FileListPosition,
  ) => void;
  refresh: () => void;
}) {
  const { t } = useTranslation();
  const transport = useTransport();
  const { client } = transport;
  const download = useMutation({
    mutationFn: async (entry: Schema<"FileEntry">) =>
      downloadFile(
        (
          await fileTransfer(
            transport,
            entry.path,
            entry.revision,
            "attachment",
          )
        ).url,
      ),
  });
  const reveal = useMutation({
    mutationFn: async (path: string) => {
      const { error } = await client.POST("/api/host/files/reveal", {
        params: { query: { path } },
      });
      if (error)
        throw new Error(t("Could not show this item in the file manager."));
    },
  });
  const queries = useQueryClient();
  const buffers = useContext(FileBuffers);
  const [operation, setOperation] = useState<Operation | null>(null);
  const [hideIgnored, setHideIgnored] = useState(
    position?.hideIgnored ?? false,
  );
  const [filter, setFilter] = useState(position?.filter ?? "");
  const restored = useRef(false);
  const target = restored.current ? undefined : position?.path;
  const [copyError, setCopyError] = useState(false);
  const copyPath = (path: string) => {
    void copyText(path).then(
      () => setCopyError(false),
      () => setCopyError(true),
    );
  };
  const git = useGitStatus(directory, true);
  const { listing, entries } = useDirectoryListing(directory, target);
  const repo = git.status.data?.pages[0]?.repository;
  const dirty = [...buffers.entries()].filter(
    ([path, buffer]) =>
      (!roots || roots.some((root) => withinRoot(path, root))) &&
      (buffer.dirty || buffer.uncertain || buffer.saving),
  );
  const [expandedPaths, setExpandedPaths] = useState(
    () => new Set(position?.expandedPaths),
  );
  function toggleFolder(path: string) {
    setExpandedPaths((previous) => {
      const next = new Set(previous);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }
  function renderEntry(entry: Schema<"FileEntry">): ReactNode {
    const change = repo
      ? git.entries.find(
          (item) =>
            gitPath(repo.root, item.path.replace(/\/$/, "")) === entry.path,
        )
      : undefined;
    if (hideIgnored && !git.status.error && change?.kind === "ignored")
      return null;
    const folder = entry.kind === "directory";
    const expanded = folder && expandedPaths.has(entry.path);
    const entryActions = (
      <MenuGroup>
        {entry.kind === "symlink" && (
          <MenuGroupLabel>
            {t("symlink")}
            {entry.link_target ? ` → ${entry.link_target}` : ""}
          </MenuGroupLabel>
        )}
        {entry.kind === "file" && unavailablePreview(entry.path) && (
          <MenuItem onClick={() => download.mutate(entry)}>
            <DownloadSimple />
            {t("Download")}
          </MenuItem>
        )}
        {fileManager && (folder || entry.kind === "file") && (
          <MenuItem onClick={() => reveal.mutate(entry.path)}>
            <FolderOpen />{" "}
            {fileManager === "finder"
              ? t("Show in Finder")
              : t("Show in File Explorer")}
          </MenuItem>
        )}
        <MenuItem onClick={() => copyPath(entry.path)}>
          <Copy /> {t("Copy path")}
        </MenuItem>
        {openTerminal && entry.kind === "directory" && (
          <MenuItem onClick={() => openTerminal(entry.path)}>
            <TerminalWindow /> {t("Open terminal here")}
          </MenuItem>
        )}
        <MenuItem onClick={() => setOperation({ kind: "move", entry })}>
          <PencilSimple /> {t("Rename or move")}
        </MenuItem>
        {entry.kind === "file" && (
          <MenuItem onClick={() => setOperation({ kind: "replace", entry })}>
            <UploadSimple /> {t("Upload replacement")}
          </MenuItem>
        )}
        <MenuSeparator />
        <MenuItem
          variant="destructive"
          onClick={() => setOperation({ kind: "delete", entry })}
        >
          <Trash /> {t("Delete")}
        </MenuItem>
      </MenuGroup>
    );
    return (
      <Fragment key={entry.path}>
        <div
          className={`${styles.fileRow} ${entry.path === path ? styles.selected : ""}`}
        >
          <ContextMenu>
            <ContextMenuTrigger
              render={<button type="button" />}
              className={styles.fileLink}
              title={entry.path}
              aria-expanded={folder ? expanded : undefined}
              ref={(button: HTMLElement | null) => {
                if (
                  !button ||
                  !position ||
                  restored.current ||
                  entry.path !== position.path
                )
                  return;
                const body = button.closest<HTMLElement>("[data-pane]");
                if (!body || body.hidden || button.closest("[inert]")) return;
                restored.current = true;
                if (position.rowOffset !== undefined) {
                  body.scrollTop +=
                    button.getBoundingClientRect().top -
                    body.getBoundingClientRect().top -
                    position.rowOffset;
                }
                button.scrollIntoView?.({ block: "nearest" });
                button.focus({ preventScroll: true });
                button.parentElement?.classList.add(styles.returnedFile!);
              }}
              onClick={(event) => {
                if (folder) return toggleFolder(entry.path);
                const button = event.currentTarget;
                const body = button.closest("[data-pane]");
                open(entry.path, entry.kind, {
                  directory,
                  path: entry.path,
                  expandedPaths: [...expandedPaths],
                  filter,
                  hideIgnored,
                  rowOffset: body
                    ? button.getBoundingClientRect().top -
                      body.getBoundingClientRect().top
                    : undefined,
                });
              }}
            >
              <FileTypeIcon name={entry.path} kind={entry.kind} />
              <span>{basename(entry.path)}</span>
              <small>
                {change
                  ? gitChangeLabel(change, t)
                  : entry.kind === "file"
                    ? `${entry.size.toLocaleString()} B`
                    : entry.kind === "symlink"
                      ? t("Link")
                      : null}
              </small>
            </ContextMenuTrigger>
            <MenuPopup align="start" sideOffset={2}>
              {entryActions}
            </MenuPopup>
          </ContextMenu>
          <Menu>
            <MenuTrigger
              render={
                <Button
                  size="icon-sm"
                  variant="ghost"
                  aria-label={t("Actions for {{name}}", {
                    name: basename(entry.path),
                  })}
                  className={styles.fileActions}
                />
              }
            >
              <DotsThree />
            </MenuTrigger>
            <MenuPopup align="end">{entryActions}</MenuPopup>
          </Menu>
        </div>
        {expanded && (
          <DirectoryChildren
            directory={entry.path}
            renderEntry={renderEntry}
            target={target}
          />
        )}
      </Fragment>
    );
  }
  return (
    <div className={styles.stack}>
      {!!dirty.length && (
        <div className={styles.buffers} aria-label={t("Local unsaved files")}>
          <small>{t("Private drafts - saved in this tab")}</small>
          {dirty.map(([name]) => (
            <Button
              key={name}
              variant="ghost"
              size="sm"
              onClick={() => open(name)}
            >
              {t("{{name}} - Unsaved", { name: basename(name) })}
            </Button>
          ))}
        </div>
      )}
      {directory && (
        <>
          <div className={styles.directoryToolbar}>
            <SearchField
              label={t("Filter loaded files in this directory")}
              placeholder={t("Filter files…")}
              value={filter}
              onChange={setFilter}
            />
            <Menu>
              <MenuTrigger
                render={
                  <Button
                    size="icon-sm"
                    variant="ghost"
                    aria-label={t("Folder actions")}
                  />
                }
              >
                <DotsThree />
              </MenuTrigger>
              <MenuPopup align="end">
                <MenuItem onClick={() => copyPath(directory)}>
                  <Copy /> {t("Copy folder path")}
                </MenuItem>
                {openTerminal && (
                  <MenuItem onClick={() => openTerminal(directory)}>
                    <TerminalWindow /> {t("Open terminal here")}
                  </MenuItem>
                )}
                {git.feature && (
                  <>
                    <MenuSeparator />
                    <MenuCheckboxItem
                      checked={hideIgnored}
                      onCheckedChange={setHideIgnored}
                    >
                      {t("Hide Git-ignored entries")}
                    </MenuCheckboxItem>
                  </>
                )}
              </MenuPopup>
            </Menu>
          </div>
          {listing.hasNextPage && (
            <small>
              {t(
                "Searching loaded files only. Load more to include the rest of this folder.",
              )}
            </small>
          )}
          <ErrorNotice inline error={reveal.error || download.error} />
          {copyError && (
            <small role="status">
              {t("Copy unavailable. Select the file path to copy it.")}
            </small>
          )}
          <ErrorNotice
            error={listing.error}
            retry={() =>
              void queries.resetQueries({
                queryKey: ["native", "directory", directory],
              })
            }
          />
          {listing.isFetching && (
            <small role="status" className={styles.directoryLoading}>
              {t("Reading directory…")}
            </small>
          )}
          {git.feature && (git.discovery.error || git.status.error) && (
            <small>
              {t(
                "Git status couldn't be loaded. Showing files without status filters.",
              )}
            </small>
          )}
          {git.status.hasNextPage && (
            <small>
              {t(
                "Git status is shown for loaded files only. Open Changes to see more.",
              )}
            </small>
          )}
          {!listing.isPlaceholderData &&
            listing.data?.pages[0]?.resolved_path !== directory &&
            listing.data && (
              <small>
                {t("Resolved directory: {{path}}", {
                  path: listing.data.pages[0]!.resolved_path,
                })}
              </small>
            )}
          <div
            className={styles.fileList}
            aria-label={t("Directory entries")}
            aria-busy={listing.isFetching}
            inert={listing.isPlaceholderData}
          >
            {entries
              .filter((entry) =>
                basename(entry.path)
                  .toLowerCase()
                  .includes(filter.toLowerCase()),
              )
              .map(renderEntry)}
            {filter &&
              entries.length > 0 &&
              !entries.some((entry) =>
                basename(entry.path)
                  .toLowerCase()
                  .includes(filter.toLowerCase()),
              ) && (
                <p className={styles.empty}>{t("No matching loaded files.")}</p>
              )}
            {!entries.length && listing.isSuccess && !listing.error && (
              <p className={styles.empty}>{t("This directory is empty.")}</p>
            )}
          </div>
          {listing.hasNextPage && (
            <Button
              variant="ghost"
              size="sm"
              disabled={listing.isFetching}
              onClick={() => void listing.fetchNextPage()}
            >
              {t("Load more files")}
            </Button>
          )}
        </>
      )}
      <ModalFrame
        bottomStickOnMobile
        open={!!operation}
        onOpenChange={(value) => {
          if (!value) setOperation(null);
        }}
        title={
          operation?.kind === "delete"
            ? t("Delete file or folder")
            : operation?.kind === "move"
              ? t("Rename or move")
              : t("Replace file from upload")
        }
        description={t(
          "These are files on the server, outside the agent's environment.",
        )}
        closeLabel={t("Close")}
      >
        {operation && (
          <FileOperation
            key={operation.kind + operation.entry.path}
            operation={operation}
            refresh={refresh}
            cancel={() => setOperation(null)}
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
class FileOperationError extends Error {}

export function FileOperation({
  operation,
  refresh,
  done,
  cancel,
}: {
  operation: Operation;
  refresh: () => void;
  done: (target?: string) => void;
  cancel?: () => void;
}) {
  const { t } = useTranslation();
  const { client, fetch } = useTransport();
  const focusedName = useRef(false);
  const [name, setName] = useState(basename(operation.entry.path));
  const [folder, setFolder] = useState(parentPath(operation.entry.path));
  const destination =
    operation.kind !== "move" ? operation.entry.path : joinPath(folder, name);
  const invalidName =
    !name.trim() ||
    name === "." ||
    name === ".." ||
    name.includes(separator(folder)) ||
    (separator(folder) === "\\" && name.includes("/"));
  const [upload, setUpload] = useState<File | null>(null);
  const [recursive, setRecursive] = useState(false);
  const [pending, setPending] = useState(false);
  const [attempted, setAttempted] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const submit = async () => {
    if (pending || attempted || invalidName || !folder.trim()) return;
    setPending(true);
    setError(null);
    setAttempted(true);
    try {
      if (operation.kind === "move")
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
          throw new FileOperationError("Choose a file no larger than 10 MiB.");
        await fetch(
          `/api/host/files/content?${new URLSearchParams({ path: destination, expected_revision: operation.entry.revision })}`,
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
      {operation.kind !== "move" && (
        <div className={styles.path}>
          {operation.entry.path}
          <DebugId
            label={t("Observed revision")}
            value={operation.entry.revision}
          />
        </div>
      )}
      {operation.kind === "delete" ? (
        <>
          <p>
            {t(
              "Permanently delete this {{kind}} for everyone using the server? Unsaved editor text is kept.",
              {
                kind:
                  operation.entry.kind === "directory"
                    ? t("directory")
                    : operation.entry.kind === "symlink"
                      ? t("symlink")
                      : t("file"),
              },
            )}
          </p>
          {operation.entry.kind === "directory" && (
            <label className={styles.check}>
              <Checkbox
                checked={recursive}
                onCheckedChange={(checked) => setRecursive(checked === true)}
              />
              {t("Recursively delete this folder and its contents")}
            </label>
          )}
        </>
      ) : operation.kind === "replace" ? (
        <p>
          {t(
            "Replace this shared file with your upload? Your unsaved edits are kept. Replacement is blocked if the file has changed.",
          )}
        </p>
      ) : (
        <>
          <TextField
            label={t("Name")}
            value={name}
            onChange={setName}
            onFocus={(event) => {
              if (operation.kind !== "move" || focusedName.current) return;
              focusedName.current = true;
              const extension =
                operation.entry.kind === "file" ? name.lastIndexOf(".") : -1;
              event.currentTarget.setSelectionRange(
                0,
                extension > 0 ? extension : name.length,
              );
              event.currentTarget.scrollLeft = 0;
            }}
          />
          <details className={layout.details}>
            <summary>
              {t("Folder - {{name}}", { name: basename(folder) })}
            </summary>
            <div>
              <TextField
                label={t("Destination folder")}
                value={folder}
                onChange={setFolder}
              />
              <p className={styles.operationHint}>
                {t("Use an absolute server path.")}
              </p>
            </div>
          </details>
        </>
      )}
      {operation.kind === "replace" && (
        <label className={styles.stack}>
          {t("Upload file (up to 10 MiB)")}
          <input
            type="file"
            onChange={(event) => {
              const file = event.target.files?.[0];
              setUpload(file ?? null);
            }}
          />
        </label>
      )}
      {operation.kind !== "delete" && operation.kind !== "replace" && (
        <p className={styles.operationHint}>
          {t(
            "Existing files aren't replaced. Use a different name or Upload replacement.",
          )}
        </p>
      )}
      {operation.kind === "delete" && recursive && (
        <p>
          {t(
            "Some files may already be deleted. Refresh before trying another action.",
          )}
        </p>
      )}
      <ErrorNotice
        error={
          error instanceof FileOperationError
            ? new Error(t("Choose a file no larger than 10 MiB."))
            : error
        }
      />
      {error != null && (
        <p>
          {t(
            "The change may already be done. Close this dialog and refresh the files before trying another action.",
          )}
        </p>
      )}
      <div className={`${styles.actions} justify-end`} data-a13n-form-actions>
        {cancel && (
          <Button
            type="button"
            variant="outline"
            disabled={pending}
            onClick={cancel}
          >
            {t("Cancel")}
          </Button>
        )}
        <Button
          type="submit"
          variant={operation.kind === "delete" ? "destructive" : "default"}
          loading={pending}
          disabled={
            attempted ||
            invalidName ||
            !folder.trim() ||
            (operation.kind === "move" &&
              destination === operation.entry.path) ||
            (operation.kind === "replace" &&
              (!upload || upload.size > 10 * 1024 * 1024))
          }
        >
          {operation.kind === "delete"
            ? t("Confirm delete")
            : operation.kind === "move"
              ? t("Save")
              : t("Replace file")}
        </Button>
      </div>
    </form>
  );
}

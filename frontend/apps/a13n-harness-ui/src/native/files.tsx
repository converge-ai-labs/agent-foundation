import { useTranslation } from "../i18n";
import { useContext, useRef, useState } from "react";
import {
  keepPreviousData,
  useInfiniteQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  Button,
  Checkbox,
  ModalFrame,
  Menu,
  MenuTrigger,
  MenuPopup,
  MenuItem,
  MenuCheckboxItem,
  MenuSeparator,
  MenuGroup,
  MenuGroupLabel,
} from "a13n-ui";
import {
  Folder,
  FilePlus,
  FolderPlus,
  UploadSimple,
  Copy,
  TerminalWindow,
  PencilSimple,
  Trash,
  File,
  Link as LinkIcon,
  DotsThree,
  CaretRight,
  Plus,
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
import { copyText } from "../platform";

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
  const { t } = useTranslation();
  const { client } = useTransport();
  const queries = useQueryClient();
  const buffers = useContext(FileBuffers);
  const [operation, setOperation] = useState<Operation | null>(null);
  const [hideIgnored, setHideIgnored] = useState(false);
  const [filter, setFilter] = useState("");
  const [copyError, setCopyError] = useState(false);
  const copyPath = (path: string) => {
    void copyText(path).then(
      () => setCopyError(false),
      () => setCopyError(true),
    );
  };
  const git = useGitStatus(directory, true);
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
  const repo = git.status.data?.pages[0]?.repository;
  const dirty = [...buffers.entries()].filter(
    ([path, buffer]) =>
      (!roots || roots.some((root) => withinRoot(path, root))) &&
      (buffer.dirty || buffer.uncertain || buffer.saving),
  );
  return (
    <div className={styles.stack}>
      {!!dirty.length && (
        <div className={styles.buffers} aria-label={t("Local unsaved files")}>
          <small>{t("Private drafts · saved in this tab")}</small>
          {dirty.map(([name]) => (
            <Button
              key={name}
              variant="ghost"
              size="sm"
              onClick={() => open(name)}
            >
              {t("{{name}} · Unsaved", { name: basename(name) })}
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
                    aria-label={t("Add file or folder")}
                  />
                }
              >
                <Plus />
              </MenuTrigger>
              <MenuPopup align="end">
                <MenuItem onClick={() => setOperation({ kind: "new-file" })}>
                  <FilePlus /> {t("New file")}
                </MenuItem>
                <MenuItem
                  onClick={() => setOperation({ kind: "new-directory" })}
                >
                  <FolderPlus /> {t("New folder")}
                </MenuItem>
                <MenuSeparator />
                <MenuItem onClick={() => setOperation({ kind: "upload" })}>
                  <UploadSimple /> {t("Upload files")}
                </MenuItem>
              </MenuPopup>
            </Menu>
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
                          ? gitChangeLabel(change, t)
                          : entry.kind === "file"
                            ? `${entry.size.toLocaleString()} B`
                            : entry.kind === "symlink"
                              ? t("Link")
                              : null}
                      </small>
                      {entry.kind === "directory" && (
                        <CaretRight size={14} aria-hidden="true" />
                      )}
                    </button>
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
                      <MenuPopup align="end">
                        <MenuGroup>
                          {entry.kind === "symlink" && (
                            <MenuGroupLabel>
                              {t("symlink")}
                              {entry.link_target
                                ? ` → ${entry.link_target}`
                                : ""}
                            </MenuGroupLabel>
                          )}
                          <MenuItem onClick={() => copyPath(entry.path)}>
                            <Copy /> {t("Copy path")}
                          </MenuItem>
                          {openTerminal && entry.kind === "directory" && (
                            <MenuItem onClick={() => openTerminal(entry.path)}>
                              <TerminalWindow /> {t("Open terminal here")}
                            </MenuItem>
                          )}
                          <MenuItem
                            onClick={() =>
                              setOperation({ kind: "move", entry })
                            }
                          >
                            <PencilSimple /> {t("Rename or move")}
                          </MenuItem>
                          {entry.kind === "file" && (
                            <MenuItem
                              onClick={() =>
                                setOperation({ kind: "replace", entry })
                              }
                            >
                              <UploadSimple /> {t("Upload replacement")}
                            </MenuItem>
                          )}
                          <MenuSeparator />
                          <MenuItem
                            variant="destructive"
                            onClick={() =>
                              setOperation({ kind: "delete", entry })
                            }
                          >
                            <Trash /> {t("Delete")}
                          </MenuItem>
                        </MenuGroup>
                      </MenuPopup>
                    </Menu>
                  </div>
                );
              })}
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
        open={!!operation}
        onOpenChange={(value) => {
          if (!value) setOperation(null);
        }}
        title={
          operation?.kind === "delete"
            ? t("Delete file or folder")
            : operation?.kind === "move"
              ? t("Rename or move")
              : operation?.kind === "replace"
                ? t("Replace file from upload")
                : operation?.kind === "upload"
                  ? t("Upload to server")
                  : operation?.kind === "new-directory"
                    ? t("Create folder")
                    : t("Create file")
        }
        description={t(
          "These are files on the server, outside the agent's environment.",
        )}
        closeLabel={t("Close")}
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
  directory,
  refresh,
  done,
  cancel,
}: {
  operation: Operation;
  directory: string;
  refresh: () => void;
  done: (target?: string) => void;
  cancel?: () => void;
}) {
  const { t } = useTranslation();
  const { client, fetch } = useTransport();
  const focusedName = useRef(false);
  const [name, setName] = useState(
    "entry" in operation ? basename(operation.entry.path) : "",
  );
  const [folder, setFolder] = useState(
    "entry" in operation ? parentPath(operation.entry.path) : directory,
  );
  const destination =
    "entry" in operation && operation.kind !== "move"
      ? operation.entry.path
      : joinPath(folder, name);
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
          throw new FileOperationError("Choose a file no larger than 10 MiB.");
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
      {"entry" in operation && operation.kind !== "move" && (
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
              {t("Folder · {{name}}", { name: basename(folder) })}
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
      {(operation.kind === "upload" || operation.kind === "replace") && (
        <label className={styles.stack}>
          {t("Upload file (up to 10 MiB)")}
          <input
            type="file"
            onChange={(event) => {
              const file = event.target.files?.[0];
              setUpload(file ?? null);
              if (file && operation.kind === "upload") setName(file.name);
            }}
          />
        </label>
      )}
      {operation.kind !== "delete" && operation.kind !== "replace" && (
        <p className={styles.operationHint}>
          {operation.kind === "new-directory"
            ? t("Choose a name that isn't already in this folder.")
            : t(
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
            ((operation.kind === "upload" || operation.kind === "replace") &&
              (!upload || upload.size > 10 * 1024 * 1024))
          }
        >
          {operation.kind === "delete"
            ? t("Confirm delete")
            : operation.kind === "move"
              ? t("Save")
              : operation.kind === "replace"
                ? t("Replace file")
                : operation.kind === "upload"
                  ? t("Upload file")
                  : t("Create")}
        </Button>
      </div>
    </form>
  );
}

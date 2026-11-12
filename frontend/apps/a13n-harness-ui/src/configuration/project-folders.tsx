import { useTranslation } from "../i18n";
import { useState } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { Button, Input, ScrollArea } from "a13n-ui";
import {
  ArrowUpIcon,
  CaretRightIcon,
  FolderIcon,
  DesktopIcon,
  PlusIcon,
  XIcon,
} from "@phosphor-icons/react";
import { useSetup, useStatus, useTransport } from "../transport/context";
import { result } from "../transport/client";
import { basename, parentPath } from "../native/buffer";
import { ErrorNotice, TextField } from "../shell/ui";
import styles from "./project-folders.module.css";

export function ProjectFolders({
  roots,
  allowEmpty = false,
  onChange,
}: {
  roots: { path: string }[];
  allowEmpty?: boolean;
  onChange: (roots: { path: string }[]) => void;
}) {
  const { t } = useTranslation();
  const status = useStatus();
  const setup = useSetup();
  const { client } = useTransport();
  const [browsing, setBrowsing] = useState(false);
  const [editing, setEditing] = useState<number | null>(null);
  const picker = useMutation({
    mutationFn: () => result(client.POST("/api/host/files/pick-directory")),
  });
  const empty = roots.findIndex((root) => !root.path.trim());
  const update = (index: number, path: string) =>
    onChange(roots.map((root, i) => (i === index ? { ...root, path } : root)));
  const select = (path: string) => {
    if (!roots.some((root) => root.path === path)) {
      if (empty >= 0) update(empty, path);
      else onChange([...roots, { path }]);
    }
    picker.reset();
    setBrowsing(false);
    setEditing(null);
  };
  const addFolder = () => {
    if (status.data?.features?.file_manager) {
      picker.mutate(undefined, {
        onSuccess: (path) => {
          if (path) select(path);
        },
        onError: () => setBrowsing(true),
      });
    } else if (status.data?.features?.host_files) {
      setBrowsing(true);
    } else {
      setEditing(empty >= 0 ? empty : roots.length);
      if (empty < 0) onChange([...roots, { path: "" }]);
    }
  };
  return (
    <div className={styles.folders}>
      <div className={styles.folderHeading}>
        <div className={styles.environmentIdentity}>
          <DesktopIcon aria-hidden />
          <div>
            <h4>{t("Local environment")}</h4>
            <p>{t("The computer running Harness.")}</p>
          </div>
        </div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={addFolder}
          loading={picker.isPending}
          disabled={
            status.isPending || browsing || (empty < 0 && roots.length >= 64)
          }
        >
          <PlusIcon />
          {t("Add folder")}
        </Button>
      </div>
      {!roots.length && (
        <p className={styles.emptyFolders}>{t("Chat files only")}</p>
      )}
      {roots.map((root, index) => {
        const label =
          index === 0
            ? t("Server directory")
            : t("Additional server directory {{number}}", { number: index });
        return (
          <div key={index} className={styles.folder}>
            <FolderIcon aria-hidden />
            {editing === index || !root.path ? (
              <Input
                aria-label={label}
                value={root.path}
                placeholder="/path/to/project"
                autoFocus={editing === index}
                autoComplete="off"
                onFocus={() => setEditing(index)}
                onChange={(event) => update(index, event.target.value)}
                onBlur={() => setEditing(null)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    event.currentTarget.blur();
                  }
                }}
              />
            ) : (
              <button
                type="button"
                className={styles.folderPath}
                title={root.path}
                onClick={() => setEditing(index)}
              >
                <strong>{basename(root.path) || root.path}</strong>
                <span dir="auto">{root.path}</span>
              </button>
            )}
            {(allowEmpty || roots.length > 1) && (
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label={t("Remove directory {{number}}", {
                  number: index + 1,
                })}
                onClick={() => {
                  setEditing(null);
                  onChange(roots.filter((_, i) => i !== index));
                }}
              >
                <XIcon />
              </Button>
            )}
          </div>
        );
      })}
      <ErrorNotice error={picker.error} />
      {browsing && (
        <DirectoryBrowser
          initialPath={
            roots.find((root) => root.path.trim())?.path ||
            setup.data?.suggested_project_path ||
            ""
          }
          select={select}
          close={() => {
            setBrowsing(false);
            picker.reset();
          }}
        />
      )}
    </div>
  );
}

type Cursor = { offset: number; revision?: string };
function DirectoryBrowser({
  initialPath,
  select,
  close,
}: {
  initialPath: string;
  select: (path: string) => void;
  close: () => void;
}) {
  const { client } = useTransport();
  const { t } = useTranslation();
  const queries = useQueryClient();
  const [path, setPath] = useState(initialPath);
  const [input, setInput] = useState(initialPath);
  const listing = useInfiniteQuery({
    queryKey: ["project-directory", path],
    initialPageParam: { offset: 0 } as Cursor,
    queryFn: ({ pageParam, signal }) =>
      result(
        client.GET("/api/host/files", {
          params: { query: { path, ...pageParam } },
          signal,
        }),
      ),
    getNextPageParam: (last): Cursor | undefined =>
      last.next_offset === null
        ? undefined
        : { offset: last.next_offset, revision: last.directory.revision },
    enabled: !!path,
    refetchOnMount: "always",
  });
  const navigate = (next: string) => {
    setInput(next);
    setPath(next);
  };
  const current = listing.data?.pages[0]?.resolved_path;
  const entries =
    listing.data?.pages
      .flatMap((page) => page.entries)
      .filter(
        (entry) => entry.kind === "directory" || entry.kind === "symlink",
      ) ?? [];
  return (
    <section
      className={styles.browser}
      aria-label={t("Browse server directories")}
      onKeyDown={(event) => {
        if (event.key === "Enter" && event.target instanceof HTMLInputElement) {
          event.preventDefault();
          if (input.trim()) navigate(input.trim());
        }
      }}
    >
      <div className={styles.row}>
        <TextField
          label={t("Browse server path")}
          value={input}
          onChange={setInput}
        />
        <Button
          type="button"
          variant="outline"
          disabled={!input.trim()}
          onClick={() => navigate(input.trim())}
        >
          {t("Go")}
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon"
          aria-label={t("Close directory browser")}
          onClick={close}
        >
          <XIcon />
        </Button>
      </div>
      <div className={styles.toolbar}>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={!path || parentPath(current || path) === (current || path)}
          onClick={() => navigate(parentPath(current || path))}
        >
          <ArrowUpIcon />
          {t("Parent folder")}
        </Button>
        <span className={styles.path} title={current || path}>
          {basename(current || path)}
        </span>
      </div>
      <ErrorNotice
        error={listing.error}
        retry={() =>
          void queries.resetQueries({
            queryKey: ["project-directory", path],
            exact: true,
          })
        }
      />
      {listing.isFetching && (
        <small role="status">{t("Reading directory…")}</small>
      )}
      <ScrollArea autoHeight scrollbarGutter className={styles.entries}>
        {entries.map((entry) => (
          <button
            type="button"
            key={entry.path}
            className={styles.entry}
            onClick={() => navigate(entry.path)}
          >
            <FolderIcon />
            <span>{basename(entry.path)}</span>
            {entry.kind === "symlink" && <small>{t("Link")}</small>}
            <CaretRightIcon />
          </button>
        ))}
        {listing.isSuccess && !entries.length && (
          <small className={styles.hint}>
            {listing.hasNextPage
              ? t("No folders in this page. Load more to continue.")
              : t("No subfolders.")}
          </small>
        )}
        {listing.hasNextPage && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            loading={listing.isFetchingNextPage}
            onClick={() => void listing.fetchNextPage()}
          >
            {t("Load more entries")}
          </Button>
        )}
      </ScrollArea>
      <div className={styles.toolbar}>
        <Button
          type="button"
          size="sm"
          disabled={!current || listing.isFetching || listing.isError}
          variant="outline"
          onClick={() => current && select(current)}
        >
          {t("Use this directory")}
        </Button>
        <Button type="button" variant="ghost" size="sm" onClick={close}>
          {t("Cancel")}
        </Button>
      </div>
    </section>
  );
}

import { useTranslation } from "../i18n";
import { useState } from "react";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Input } from "a13n-ui";
import {
  ArrowUpIcon,
  CaretRightIcon,
  FolderIcon,
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
  compact = false,
  onChange,
}: {
  roots: { path: string }[];
  allowEmpty?: boolean;
  compact?: boolean;
  onChange: (roots: { path: string }[]) => void;
}) {
  const { t } = useTranslation();
  const status = useStatus();
  const setup = useSetup();
  const [browsing, setBrowsing] = useState<number | null>(null);
  const update = (index: number, path: string) =>
    onChange(roots.map((root, i) => (i === index ? { ...root, path } : root)));
  return (
    <div className={`${styles.folders} ${compact ? styles.compact : ""}`}>
      {compact && roots.length > 0 && (
        <p className={styles.label}>{t("Working folders")}</p>
      )}
      {!compact && (
        <p className={styles.hint}>
          {t(
            "Choose existing folders on the server or in its container. The first is your workspace. Files stay in place.",
          )}
        </p>
      )}
      {roots.map((root, index) => (
        <div key={index} className={styles.folder}>
          <div className={`${styles.row} ${styles.directoryRow}`}>
            {compact ? (
              <Input
                aria-label={
                  index === 0
                    ? t("Server directory")
                    : t("Additional server directory {{number}}", {
                        number: index,
                      })
                }
                value={root.path}
                title={root.path}
                placeholder="/path/to/project"
                autoComplete="off"
                onChange={(event) => update(index, event.target.value)}
              />
            ) : (
              <TextField
                label={
                  index === 0
                    ? t("Server directory")
                    : t("Additional server directory {{number}}", {
                        number: index,
                      })
                }
                value={root.path}
                onChange={(value) => update(index, value)}
              />
            )}
            {status.data?.features?.host_files && (
              <Button
                type="button"
                variant="outline"
                className={styles.browseButton}
                aria-label={t("Browse directory {{number}}", {
                  number: index + 1,
                })}
                aria-expanded={browsing === index}
                onClick={() => setBrowsing(browsing === index ? null : index)}
              >
                <FolderIcon />
                <span>{t("Browse")}</span>
              </Button>
            )}
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              disabled={!allowEmpty && roots.length <= 1}
              aria-label={t("Remove directory {{number}}", {
                number: index + 1,
              })}
              onClick={() => {
                setBrowsing(null);
                onChange(roots.filter((_, i) => i !== index));
              }}
            >
              <XIcon />
            </Button>
          </div>
          {browsing === index && status.data?.features?.host_files && (
            <DirectoryBrowser
              key={index}
              initialPath={
                root.path.trim() || setup.data?.suggested_project_path || ""
              }
              select={(path) => {
                update(index, path);
                setBrowsing(null);
              }}
              close={() => setBrowsing(null)}
            />
          )}
        </div>
      ))}
      <div>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className={compact ? styles.addFolder : undefined}
          disabled={roots.length >= 64}
          onClick={() => onChange([...roots, { path: "" }])}
        >
          <PlusIcon />
          {roots.length ? t("Add folder") : t("Add local folder")}
        </Button>
      </div>
      {roots.length > 0 && status.data && !status.data.features?.host_files && (
        <small className={styles.hint}>
          {t(
            "Turn on computer sharing to browse folders, or enter a path manually.",
          )}
        </small>
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
      <div className={`${styles.entries} a13n-scrollbar`}>
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
      </div>
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

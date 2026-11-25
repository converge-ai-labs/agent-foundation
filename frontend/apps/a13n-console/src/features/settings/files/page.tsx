import {
  DownloadSimpleIcon,
  ArchiveIcon,
  CaretRightIcon,
  FileIcon,
  FileAudioIcon,
  FileImageIcon,
  FileTextIcon,
  FileVideoIcon,
  FolderSimpleIcon,
} from "@phosphor-icons/react";
import { Button, ChoiceField, MenuItem, ModalFrame } from "a13n-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { ApiError } from "../../../service-client";
import { data, ifMatch, rowTag, type Schema } from "../../../shared/api";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceTable,
  Toolbar,
  useCursor,
} from "../../../shared/collection";
import { downloadBlob } from "../../../shared/download";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../../shared/feedback";
import { ResourceIdentity } from "../../../shared/identity";
import settings from "../settings.module.css";
import styles from "./files.module.css";

export const FILES_DESCRIPTION =
  "Conversation uploads and files published by agents are visible to anyone with workspace read access.";

type Asset = Schema["ListedAsset"];
type Storage = Schema["WorkspaceStorage"];
type SourceKind = "user_upload" | "agent_run";
type Status = "all" | "active" | "retired";

function fileIcon(contentType: string) {
  switch (contentType.split("/")[0]) {
    case "image":
      return FileImageIcon;
    case "audio":
      return FileAudioIcon;
    case "video":
      return FileVideoIcon;
    case "text":
      return FileTextIcon;
    default:
      return FileIcon;
  }
}

function formatBytes(bytes: number, locale?: string) {
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB"];
  let unit = 0;
  while (bytes >= 1024 && unit < units.length - 1) {
    unit += 1;
    bytes /= 1024;
  }
  return `${new Intl.NumberFormat(locale, { maximumFractionDigits: 1 }).format(bytes)} ${units[unit]}`;
}

function UsageNumbers({
  usage,
}: {
  usage: Pick<
    Storage,
    "total_bytes" | "active_bytes" | "retired_bytes" | "staged_upload_bytes"
  >;
}) {
  const { t, i18n } = useTranslation();
  const metrics = [
    [t("Total"), usage.total_bytes],
    [t("Active"), usage.active_bytes],
    [t("Retired"), usage.retired_bytes],
    [t("Staged uploads"), usage.staged_upload_bytes],
  ] as const;
  return (
    <dl className={styles.metrics}>
      {metrics.map(([label, bytes]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{formatBytes(bytes, i18n.resolvedLanguage)}</dd>
        </div>
      ))}
    </dl>
  );
}

export function WorkspaceFiles() {
  const { t, i18n } = useTranslation();
  const { workspace, basePath, can } = useWorkspace();
  const client = useClient().workspace(workspace.id);
  const cache = useQueryClient();
  const [search, setSearch] = useState("");
  const [source, setSource] = useState<SourceKind | "all">("all");
  const [status, setStatus] = useState<Status>("all");
  const [creator, setCreator] = useState("");
  const [confirm, setConfirm] = useState<Asset | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const filters = {
    query: search.trim().slice(0, 255) || undefined,
    source_kind: source === "all" ? undefined : source,
    status,
    created_by_id: creator || undefined,
  };
  const page = useCursor(filters);
  const filesKey = ["workspace-files", workspace.id];
  const storageKey = ["workspace-storage", workspace.id];
  const files = useQuery({
    queryKey: [...filesKey, filters, page.cursor],
    queryFn: async ({ signal }) =>
      data(
        await client.GET("/api/v1/assets", {
          params: { query: { ...filters, cursor: page.cursor, limit: 25 } },
          signal,
        }),
      ),
  });
  const usage = useQuery({
    queryKey: storageKey,
    queryFn: async ({ signal }) =>
      data(await client.GET("/api/v1/storage", { signal })),
  });
  const download = useMutation({
    mutationFn: async (asset: Asset) => {
      const blob = data(
        await client.GET("/api/v1/assets/{asset_id}/content", {
          params: { path: { asset_id: asset.id } },
          parseAs: "blob",
        }),
      );
      downloadBlob(blob, asset.name);
    },
  });
  const refreshAfterRetire = () => {
    void cache.invalidateQueries({ queryKey: filesKey });
    void cache.invalidateQueries({ queryKey: storageKey });
    void cache.invalidateQueries({ queryKey: ["asset", workspace.id] });
  };
  const retire = useMutation({
    mutationFn: async (asset: Asset) =>
      data(
        await client.DELETE("/api/v1/assets/{asset_id}", {
          params: {
            path: { asset_id: asset.id },
            header: ifMatch(rowTag(asset)),
          },
        }),
      ),
    onSuccess: () => {
      setConfirmOpen(false);
      refreshAfterRetire();
    },
  });
  const stale = retire.error instanceof ApiError && retire.error.status === 412;
  const count = files.data?.total_count;
  const number = new Intl.NumberFormat(i18n.resolvedLanguage);
  const filtering = Boolean(
    filters.query || filters.source_kind || creator || status !== "all",
  );

  return (
    <>
      <header className={settings.heading}>
        <div>
          <div className={styles.title}>
            <h1>{t("Files")}</h1>
            {count !== undefined && <span>{number.format(count)}</span>}
          </div>
          <p>{t(FILES_DESCRIPTION)}</p>
        </div>
      </header>
      <section className={styles.usage} aria-label={t("Storage usage")}>
        {usage.isPending ? (
          <Loading variant="list" rows={1} />
        ) : usage.error ? (
          <ErrorNotice error={usage.error} retry={() => void usage.refetch()} />
        ) : (
          usage.data && (
            <>
              <UsageNumbers usage={usage.data} />
              {usage.data.by_creator.length > 0 && (
                <details className={styles.breakdown}>
                  <summary>
                    <CaretRightIcon aria-hidden="true" />
                    {t("Usage by creator")}
                  </summary>
                  <ul>
                    {usage.data.by_creator.map((entry) => (
                      <li key={entry.created_by_id}>
                        <span className={styles.breakdownName}>
                          {entry.creator_name}
                        </span>
                        <UsageNumbers usage={entry} />
                      </li>
                    ))}
                  </ul>
                </details>
              )}
            </>
          )
        )}
      </section>
      <div className={styles.toolbar}>
        <Toolbar
          search={search}
          onSearchChange={setSearch}
          searchLabel={t("Search by filename")}
          filters={
            <>
              <ChoiceField
                variant="filter"
                label={t("Source")}
                value={source}
                onValueChange={(value) =>
                  setSource(value as SourceKind | "all")
                }
                options={[
                  { value: "all", label: t("All") },
                  { value: "user_upload", label: t("User upload") },
                  { value: "agent_run", label: t("Agent run") },
                ]}
              />
              <ChoiceField
                variant="filter"
                label={t("Status")}
                value={status}
                onValueChange={(value) => setStatus(value as Status)}
                options={[
                  { value: "all", label: t("All") },
                  { value: "active", label: t("Active") },
                  { value: "retired", label: t("Retired") },
                ]}
              />
              <ChoiceField
                variant="filter"
                label={t("Creator")}
                value={creator || "all"}
                onValueChange={(value) =>
                  setCreator(value === "all" ? "" : value)
                }
                options={[
                  { value: "all", label: t("All") },
                  ...(usage.data?.by_creator.map((entry) => ({
                    value: entry.created_by_id,
                    label: entry.creator_name,
                  })) ?? []),
                ]}
                disabled={!usage.data?.by_creator.length}
              />
            </>
          }
        />
      </div>
      <ErrorNotice
        error={download.error}
        retry={() => download.variables && download.mutate(download.variables)}
      />
      {files.isPending ? (
        <Loading variant="table" columns={6} rows={4} />
      ) : files.error ? (
        <ErrorNotice error={files.error} retry={() => void files.refetch()} />
      ) : (
        files.data && (
          <>
            {files.data.items.length > 0 ? (
              <ResourceTable
                items={files.data.items}
                caption={t("Files")}
                className={styles.table}
                columns={[
                  {
                    label: t("File"),
                    tone: "primary",
                    dataColumn: "file",
                    render: (asset) => {
                      const Icon = fileIcon(asset.content_type);
                      return (
                        <ResourceIdentity
                          name={asset.name}
                          description={asset.content_type}
                          icon={<Icon size={16} aria-hidden="true" />}
                        />
                      );
                    },
                  },
                  {
                    label: t("Source / Author"),
                    render: (asset) => (
                      <div className={styles.author}>
                        <span title={asset.creator_name}>
                          {asset.creator_name}
                        </span>
                        {asset.thread_id && asset.session_id ? (
                          <Link
                            to={`${basePath}/sessions/${asset.session_id}/threads/${asset.thread_id}`}
                          >
                            {t("Agent run")}
                            <CaretRightIcon size={12} aria-hidden="true" />
                          </Link>
                        ) : (
                          <small>
                            {asset.source ? t("Agent run") : t("User upload")}
                          </small>
                        )}
                      </div>
                    ),
                  },
                  {
                    label: t("Size"),
                    align: "right",
                    render: (asset) => (
                      <span className="whitespace-nowrap tabular-nums">
                        {formatBytes(asset.size, i18n.resolvedLanguage)}
                      </span>
                    ),
                  },
                  {
                    label: t("Status"),
                    render: (asset) => (
                      <StatePill
                        state={asset.retired_at ? "retired" : "active"}
                        label={asset.retired_at ? t("Retired") : t("Active")}
                      />
                    ),
                  },
                  {
                    label: t("Created"),
                    render: (asset) => <Timestamp value={asset.created_at} />,
                  },
                  {
                    label: t("Download"),
                    header: <span className="sr-only">{t("Download")}</span>,
                    dataColumn: "download",
                    render: (asset) => (
                      <Button
                        size="sm"
                        variant="ghost"
                        aria-label={t("Download {{name}}", {
                          name: asset.name,
                        })}
                        disabled={download.isPending}
                        onClick={() => download.mutate(asset)}
                      >
                        <DownloadSimpleIcon size={15} aria-hidden="true" />
                        {t("Download")}
                      </Button>
                    ),
                  },
                ]}
                rowMenuVisibility="always"
                rowMenu={
                  can("write")
                    ? (asset) => (
                        <MenuItem
                          disabled={!!asset.retired_at}
                          onClick={() => {
                            retire.reset();
                            setConfirm(asset);
                            setConfirmOpen(true);
                          }}
                        >
                          <ArchiveIcon size={16} aria-hidden="true" />
                          {t("Retire")}
                        </MenuItem>
                      )
                    : undefined
                }
              />
            ) : filtering ? (
              <p className={styles.noResults}>{t("No matching files")}</p>
            ) : (
              <Empty
                icon={<FolderSimpleIcon aria-hidden="true" />}
                title={t("No files yet")}
                description={t(
                  "Files uploaded in conversations or published by agents appear here.",
                )}
              />
            )}
            {(count !== 0 || page.previous) && (
              <CollectionFooter
                count={t("{{shown}} of {{total}} files", {
                  shown: number.format(files.data.items.length),
                  total: number.format(files.data.total_count),
                })}
              >
                <Pagination page={page} next={files.data.next_cursor} />
              </CollectionFooter>
            )}
          </>
        )
      )}
      {confirm && (
        <ModalFrame
          open={confirmOpen}
          onOpenChange={(open, details) => {
            if (!open && retire.isPending) details.cancel();
            else setConfirmOpen(open);
          }}
          onOpenChangeComplete={(open) => {
            if (!open) setConfirm(null);
          }}
          size="md"
          title={t("Retire file")}
          description={t(
            "Retired files remain downloadable and count toward storage, but cannot be attached to new messages.",
          )}
          closeLabel={t("Close")}
          footer={
            <>
              <Button
                variant="outline"
                disabled={retire.isPending}
                onClick={() => setConfirmOpen(false)}
              >
                {t("Cancel")}
              </Button>
              <Button
                variant="destructive"
                loading={retire.isPending}
                disabled={stale}
                onClick={() => retire.mutate(confirm)}
              >
                {t("Retire")}
              </Button>
            </>
          }
        >
          <p className={styles.confirmName}>{confirm.name}</p>
          <ErrorNotice
            error={retire.error}
            retry={
              stale
                ? () => {
                    setConfirmOpen(false);
                    refreshAfterRetire();
                  }
                : undefined
            }
          />
        </ModalFrame>
      )}
    </>
  );
}

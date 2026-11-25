import { ArrowUpRightIcon, DownloadSimpleIcon } from "@phosphor-icons/react";
import { Button } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { ErrorNotice, Loading } from "../../../shared/feedback";
import { CodeBlock } from "../../../shared/forms/code-block";
import { MarkdownContent } from "../../../shared/markdown";
import { readAsset, useAssetDownload, type Asset } from "./assets";
import {
  csvPreview,
  formatJson,
  previewFormat,
  type PreviewFormat,
} from "./preview-data";
import styles from "./files.module.css";

export function FilePreview({ asset }: { asset: Asset }) {
  const { t, i18n } = useTranslation();
  const client = useClient();
  const { workspace } = useWorkspace();
  const download = useAssetDownload(client, workspace.id, asset.id, asset.name);
  const format = previewFormat(asset);
  const formatSize = new Intl.NumberFormat(i18n.language, {
    maximumFractionDigits: 1,
  });
  const size =
    asset.size >= 1024
      ? t("{{size}} KiB", { size: formatSize.format(asset.size / 1024) })
      : t("{{size}} B", { size: formatSize.format(asset.size) });
  return (
    <section aria-label={asset.name} className={styles.preview}>
      <div className={styles.fileHeader}>
        <div className={styles.fileIdentity}>
          <h3 title={asset.name}>{asset.name}</h3>
          <small>
            {asset.content_type} · {size}
          </small>
        </div>
        <Button
          variant="secondary"
          size="sm"
          disabled={!!asset.retired_at}
          loading={download.isPending}
          onClick={() => download.mutate()}
        >
          <DownloadSimpleIcon size={14} aria-hidden="true" />
          {t("Download")}
        </Button>
      </div>
      <ErrorNotice error={download.error} retry={() => download.mutate()} />
      {asset.retired_at ? (
        <p className={styles.notice}>{t("File retired")}</p>
      ) : !format ? (
        <p className={styles.notice}>
          {t("This file type is available to download.")}
        </p>
      ) : asset.size > format.limit ? (
        <p className={styles.notice}>
          {t(
            "This file is too large to preview. Download it to view the full file.",
          )}
        </p>
      ) : (
        <PreviewContent asset={asset} format={format} />
      )}
    </section>
  );
}

type Preview =
  | { kind: "too-large" | "unreadable" }
  | { kind: "text"; text: string }
  | { kind: "media"; blob: Blob; media: "image" | "pdf" };

function PreviewContent({
  asset,
  format,
}: {
  asset: Asset;
  format: PreviewFormat;
}) {
  const { t } = useTranslation();
  const client = useClient();
  const { workspace } = useWorkspace();
  const content = useQuery({
    queryKey: [
      "asset-preview",
      workspace.id,
      asset.id,
      asset.digest,
      format.kind,
    ],
    gcTime: 0,
    staleTime: Infinity,
    retry: false,
    queryFn: async ({ signal }): Promise<Preview> => {
      const blob = await readAsset(client, workspace.id, asset.id, signal);
      if (blob.size > format.limit) return { kind: "too-large" };
      if (format.kind === "image" || format.kind === "pdf") {
        if (
          format.kind === "pdf" &&
          (await blob.slice(0, 5).text()) !== "%PDF-"
        )
          return { kind: "unreadable" };
        return {
          kind: "media",
          blob: blob.slice(0, blob.size, format.mime),
          media: format.kind,
        };
      }
      return { kind: "text", text: await blob.text() };
    },
  });
  if (content.isPending) return <Loading variant="code" />;
  if (content.isError)
    return (
      <ErrorNotice error={content.error} retry={() => void content.refetch()} />
    );
  const preview = content.data;
  if (preview.kind === "too-large")
    return (
      <p className={styles.notice}>
        {t(
          "This file is too large to preview. Download it to view the full file.",
        )}
      </p>
    );
  if (preview.kind === "unreadable")
    return (
      <p className={styles.notice}>{t("This file could not be previewed.")}</p>
    );
  return preview.kind === "media" ? (
    <MediaPreview blob={preview.blob} kind={preview.media} name={asset.name} />
  ) : (
    preview.kind === "text" && (
      <TextPreview text={preview.text} format={format} />
    )
  );
}

function MediaPreview({
  blob,
  kind,
  name,
}: {
  blob: Blob;
  kind: "image" | "pdf";
  name: string;
}) {
  const { t } = useTranslation();
  const [url, setUrl] = useState<string>();
  const [imageFailed, setImageFailed] = useState(false);
  useEffect(() => {
    const objectUrl = URL.createObjectURL(blob);
    setUrl(objectUrl);
    return () => URL.revokeObjectURL(objectUrl);
  }, [blob]);
  if (!url) return <Loading variant="code" />;
  if (kind === "image")
    return imageFailed ? (
      <p className={styles.notice}>{t("This file could not be previewed.")}</p>
    ) : (
      <div className={styles.imageFrame}>
        <img src={url} alt={name} onError={() => setImageFailed(true)} />
      </div>
    );
  return (
    <>
      <a
        className={styles.openPdf}
        href={url}
        target="_blank"
        rel="noopener noreferrer"
      >
        {t("Open in browser")} <ArrowUpRightIcon size={14} aria-hidden="true" />
      </a>
      {navigator.pdfViewerEnabled !== false && (
        <iframe className={styles.pdf} src={url} title={name} />
      )}
    </>
  );
}

function TextPreview({
  text,
  format,
}: {
  text: string;
  format: PreviewFormat;
}) {
  const { t } = useTranslation();
  const [source, setSource] = useState(false);
  const formatted = useMemo(
    () => (format.kind === "json" ? formatJson(text) : text),
    [format.kind, text],
  );
  if (!text) return <p className={styles.notice}>{t("This file is empty.")}</p>;
  return (
    <>
      {format.kind === "markdown" && (
        <div
          className={styles.mode}
          role="group"
          aria-label={t("Markdown view")}
        >
          <Button
            size="sm"
            variant={source ? "ghost" : "secondary"}
            aria-pressed={!source}
            onClick={() => setSource(false)}
          >
            {t("Preview")}
          </Button>
          <Button
            size="sm"
            variant={source ? "secondary" : "ghost"}
            aria-pressed={source}
            onClick={() => setSource(true)}
          >
            {t("Source")}
          </Button>
        </div>
      )}
      {format.kind === "markdown" && !source ? (
        <MarkdownContent text={text} />
      ) : format.kind === "csv" ? (
        <CsvPreview text={text} />
      ) : (
        <CodeBlock sourceLabel={t("File source")}>
          <code
            className={
              "language" in format && format.language
                ? `language-${format.language}`
                : undefined
            }
          >
            {formatted}
          </code>
        </CodeBlock>
      )}
    </>
  );
}

function CsvPreview({ text }: { text: string }) {
  const { t } = useTranslation();
  const {
    rows: [header, ...rows],
    truncated,
  } = useMemo(() => csvPreview(text), [text]);
  const columns = Math.max(
    header?.length ?? 0,
    ...rows.map((row) => row.length),
  );
  return (
    <>
      <div
        role="region"
        aria-label={t("CSV preview")}
        tabIndex={0}
        className={`${styles.tableFrame} a13n-scrollbar`}
      >
        <table>
          <thead>
            <tr>
              {Array.from({ length: columns }, (_, col) => (
                <th scope="col" key={col}>
                  {header?.[col]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={index}>
                {Array.from({ length: columns }, (_, col) => (
                  <td key={col}>{row[col]}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {truncated && (
        <p className={styles.notice}>
          {t("The table shows the first 100 rows and 30 columns.")}
        </p>
      )}
    </>
  );
}

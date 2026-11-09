import { useTranslation } from "../i18n";
import { useContext, useEffect, useReducer, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import {
  ArrowClockwise,
  DownloadSimple,
  FloppyDisk,
  MagnifyingGlass,
  TextAlignLeft,
} from "@phosphor-icons/react";
import { ApiError, result } from "../transport/client";
import { useTransport } from "../transport/context";
import { SourceEditor } from "../configuration/editor";
import type {
  EditorCursor,
  SourceEditorHandle,
} from "../configuration/editor-view";
import { ErrorNotice } from "../shell/ui";
import { DebugOnly } from "../shell/debug";
import { ConfirmAction } from "../shell/confirm-action";
import { MessageText } from "../conversations/message-text";
import {
  basename,
  FileBuffer,
  FileBuffers,
  mixedLineEndings,
  type LineRange,
} from "./buffer";
import { CaptureContext, downloadBlob } from "./capture";
import { FileMedia } from "./file-media";
import { downloadFile, fileTransfer } from "./file-transfer";
import { mediaKind, MAX_MEDIA_BYTES } from "./media-kind";
import { modifierLabel } from "../platform";
import shell from "../shell/workbench.module.css";
import styles from "./native.module.css";

export function FileView({
  path,
  threadId,
  refresh,
  open,
  line,
  onBufferChange,
}: {
  path: string;
  threadId?: string;
  refresh: () => void;
  open: (path: string) => void;
  line?: number;
  onBufferChange?: () => void;
}) {
  const { t } = useTranslation();
  const transport = useTransport();
  const { client } = transport;
  const buffers = useContext(FileBuffers);
  const [, render] = useReducer((value: number) => value + 1, 0);
  const [error, setError] = useState<unknown>(null);
  const [message, setMessage] = useState("");
  const [wrap, setWrap] = useState(() => /\.(?:md|markdown|txt)$/i.test(path));
  const editor = useRef<SourceEditorHandle>(null);
  const [cursor, setCursor] = useState<EditorCursor>();
  const [imageAttempt, setImageAttempt] = useState(0);
  const [mode, setMode] = useState<"preview" | "text">(() =>
    /\.(?:md|markdown)$/i.test(path) && line === undefined ? "preview" : "text",
  );
  const [textOpened, setTextOpened] = useState(mode === "text");
  const [selection, setSelection] = useState<LineRange>();
  useEffect(() => {
    if (line !== undefined) {
      setMode("text");
      setTextOpened(true);
    }
  }, [line]);
  const read = useQuery({
    queryKey: ["native", "text", path],
    queryFn: async ({ signal }) => {
      const info = await result(
        client.GET("/api/host/files/info", {
          params: { query: { path } },
          signal,
        }),
      );
      // .ts is both TypeScript and an MPEG transport stream suffix. The text
      // endpoint checks the actual bytes; binary streams keep their media view.
      const next =
        mediaKind(info.media_type) && !/\.ts$/i.test(path)
          ? info
          : {
              ...(await result(
                client.GET("/api/host/files/text", {
                  params: {
                    query: { path, expected_revision: info.entry.revision },
                  },
                  signal,
                }),
              )),
              media_type: info.media_type,
            };
      const existing = buffers.get(path);
      if (existing) existing.observe(next);
      else buffers.set(path, new FileBuffer(next));
      return next;
    },
    refetchOnMount: "always",
  });
  const buffer = buffers.get(path);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (
        [...buffers.values()].some(
          (entry) => entry.dirty || entry.uncertain || entry.saving,
        )
      )
        event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [buffers]);
  if (!buffer)
    return (
      <>
        <ErrorNotice error={read.error} retry={() => void read.refetch()} />
        {read.isPending && <p role="status">{t("Reading file…")}</p>}
      </>
    );
  const symlink = buffer.base.resolved_path !== path;
  const save = async () => {
    if (
      !buffer.dirty ||
      buffer.saving ||
      buffer.uncertain ||
      buffer.conflict ||
      symlink ||
      read.error
    )
      return;
    const submitted = buffer.value;
    buffer.saving = true;
    render();
    setError(null);
    setMessage("");
    try {
      const entry = await result(
        client.PUT("/api/host/files/text", {
          body: {
            path,
            text: submitted,
            expected_revision: buffer.base.entry.revision,
          },
        }),
      );
      buffer.saved(entry, submitted);
      setMessage(
        buffer.dirty
          ? "Saved the submitted version. Newer typing is still unsaved."
          : "File saved.",
      );
    } catch (failure) {
      setError(failure);
      buffer.uncertain = !(
        failure instanceof ApiError &&
        failure.status >= 400 &&
        failure.status < 500
      );
      if (buffer.uncertain)
        setMessage(
          "Couldn't confirm the save. Refresh and check the file before saving again.",
        );
    } finally {
      buffer.saving = false;
      render();
      onBufferChange?.();
      refresh();
    }
  };
  const download = async () => {
    setError(null);
    try {
      const access = await fileTransfer(
        transport,
        path,
        buffer.base.entry.revision,
        "attachment",
      );
      downloadFile(access.url);
    } catch (failure) {
      setError(failure);
    }
  };
  const editable = buffer.base.presentation === "text";
  const markdown = editable && /\.(?:md|markdown)$/i.test(path);
  const media = !editable ? mediaKind(buffer.base.media_type) : null;
  const refreshContent = async () => {
    const revision = buffer.base.entry.revision;
    const next = await read.refetch();
    // A changed revision already replaces the preview; an unchanged one must
    // still allow retrying a failed fetch or decode.
    if (next.isSuccess && next.data.entry.revision === revision)
      setImageAttempt((value) => value + 1);
  };
  return (
    <section className={styles.file} aria-label={t("File content")}>
      {symlink && (
        <div className={styles.notice}>
          {t(
            "Resolved target: {{path}}. This path is read-only; select the target explicitly to edit it.",
            { path: buffer.base.resolved_path },
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => open(buffer.base.resolved_path)}
          >
            {t("Open target")}
          </Button>
        </div>
      )}
      <div className={styles.fileToolbar}>
        <div className={styles.actions} aria-label={t("File actions")}>
          {editable && (buffer.dirty || buffer.saving || buffer.uncertain) && (
            <Button
              size="sm"
              disabled={
                !buffer.dirty ||
                buffer.saving ||
                buffer.conflict ||
                buffer.uncertain ||
                symlink ||
                !!read.error
              }
              loading={buffer.saving}
              onClick={() => void save()}
            >
              <FloppyDisk />
              {t("Save")}
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={t("Refresh")}
            title={t("Refresh file")}
            onClick={() => void refreshContent()}
            disabled={buffer.saving || read.isFetching}
          >
            <ArrowClockwise />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={t("Download")}
            title={t("Download file")}
            onClick={() => void download()}
            disabled={read.isFetching || !!read.error}
          >
            <DownloadSimple />
          </Button>
          {editable && buffer.dirty && (
            <Button
              variant="outline"
              size="sm"
              onClick={() =>
                downloadBlob(
                  new Blob([buffer.value], {
                    type: "text/plain;charset=utf-8",
                  }),
                  basename(path),
                )
              }
            >
              <DownloadSimple />
              {t("Download draft")}
            </Button>
          )}
        </div>
        {editable && (
          <div className={styles.viewControls} aria-label={t("File view")}>
            {markdown && (
              <>
                <Button
                  variant={mode === "preview" ? "secondary" : "ghost"}
                  size="sm"
                  aria-pressed={mode === "preview"}
                  onClick={() => setMode("preview")}
                >
                  {t("Preview")}
                </Button>
                <Button
                  variant={mode === "text" ? "secondary" : "ghost"}
                  size="sm"
                  aria-pressed={mode === "text"}
                  onClick={() => {
                    setMode("text");
                    setTextOpened(true);
                  }}
                >
                  {t("Text")}
                </Button>
              </>
            )}
            {mode === "text" && (
              <>
                <Button
                  size="sm"
                  variant={wrap ? "secondary" : "ghost"}
                  aria-pressed={wrap}
                  title={t("Wrap long lines")}
                  onClick={() => {
                    setWrap(!wrap);
                    editor.current?.focus();
                  }}
                >
                  <TextAlignLeft />
                  {t("Wrap")}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  title={t("Find in file ({{shortcut}} F)", {
                    shortcut: modifierLabel(),
                  })}
                  onClick={() => editor.current?.find()}
                >
                  <MagnifyingGlass />
                  {t("Find")}
                </Button>
              </>
            )}
          </div>
        )}
      </div>
      <ErrorNotice
        error={error || read.error}
        retry={() => void read.refetch()}
      />
      {message && <p role="status">{t(message)}</p>}
      {(buffer.conflict || buffer.uncertain) && (
        <div className={styles.notice}>
          <strong>
            {buffer.uncertain
              ? t("Save outcome needs inspection")
              : t("Disk revision changed")}
          </strong>
          <p>
            {t(
              "Your edits are kept. Refresh and review the saved file before choosing which version to keep.",
            )}
          </p>
          <details>
            <summary>{t("Show latest saved version")}</summary>
            <p>
              {t("Revision: {{revision}} - {{presentation}}", {
                revision: buffer.observed.entry.revision,
                presentation: buffer.observed.presentation,
              })}
            </p>
            {buffer.observed.text != null && (
              <SourceEditor
                value={buffer.observed.text}
                language="plain"
                filename={path}
                readOnly
                label={t("Latest disk text")}
              />
            )}
          </details>
          <div className={styles.actions}>
            <ConfirmAction
              key={`disk:${path}:${buffer.observed.entry.revision}`}
              trigger={
                <Button
                  variant="outline"
                  size="sm"
                  disabled={read.isFetching || !!read.error}
                >
                  {t("Use disk version")}
                </Button>
              }
              title={t("Replace your local text?")}
              description={t(
                "Replace your edits with this saved version? Download your edits first if you need a copy.",
              )}
              confirmLabel={t("Use disk version")}
              destructive
              onConfirm={() => {
                if (read.isFetching || read.error) return;
                buffer.adopt(false);
                setSelection(undefined);
                setError(null);
                setMessage("");
                render();
              }}
            />
            <ConfirmAction
              key={`local:${path}:${buffer.observed.entry.revision}`}
              trigger={
                <Button
                  variant="outline"
                  size="sm"
                  disabled={
                    read.isFetching ||
                    !!read.error ||
                    buffer.observed.presentation !== "text" ||
                    buffer.observed.resolved_path !== path
                  }
                >
                  {t("Keep local text")}
                </Button>
              }
              title={t("Keep your local text?")}
              description={t(
                "Keep your edits? Your next save will replace the file version you just reviewed.",
              )}
              confirmLabel={t("Keep local text")}
              onConfirm={() => {
                if (
                  read.isFetching ||
                  read.error ||
                  buffer.observed.presentation !== "text" ||
                  buffer.observed.resolved_path !== path
                )
                  return;
                buffer.adopt(true);
                setError(null);
                setMessage(
                  "Local text retained. Save explicitly to replace the inspected disk version.",
                );
                render();
              }}
            />
          </div>
        </div>
      )}
      {editable && mixedLineEndings(buffer.base.text ?? "") && (
        <p>
          {t(
            "This file mixes line endings. Editing uses the first style throughout. Viewing or attaching leaves the file unchanged.",
          )}
        </p>
      )}
      {editable ? (
        <>
          {mode === "preview" && markdown && (
            <article
              className={styles.markdownPreview}
              aria-label={t("Markdown preview")}
            >
              <MessageText text={buffer.value} />
            </article>
          )}
          {textOpened && (
            <div className={styles.editor} hidden={mode !== "text"}>
              <SourceEditor
                ref={editor}
                onCursor={setCursor}
                value={buffer.value}
                language="plain"
                filename={path}
                line={line}
                wrap={wrap}
                position={buffer.position}
                onPosition={(position) => {
                  buffer.position = position;
                }}
                onSave={() => void save()}
                label={t("File text: {{path}}", { path })}
                fill
                readOnly={symlink}
                onSelection={setSelection}
                onChange={(value) => {
                  buffer.value = value;
                  setMessage("");
                  render();
                  onBufferChange?.();
                }}
              />
            </div>
          )}
        </>
      ) : media ? (
        media !== "image" || buffer.base.entry.size <= MAX_MEDIA_BYTES ? (
          <FileMedia
            key={`${path}:${buffer.base.entry.revision}:${imageAttempt}`}
            path={path}
            revision={buffer.base.entry.revision}
            retry={() => void refreshContent()}
            kind={media}
          />
        ) : (
          <div className={styles.empty}>
            <h3>{t("Image exceeds the preview limit")}</h3>
            <p>
              {t(
                "Image too large to preview. Download it to view. The preview limit is 10 MiB.",
              )}
            </p>
          </div>
        )
      ) : (
        <div className={styles.empty}>
          <h3>
            {buffer.base.presentation === "binary"
              ? t("Binary file")
              : t("File exceeds the text editor limit")}
          </h3>
          <p>
            {t(
              "Edit UTF-8 text files up to 512 KiB, without NUL characters. Upload or attach files up to 10 MiB. Download larger files to view them.",
            )}
          </p>
        </div>
      )}
      <footer className={styles.fileFooter}>
        <CaptureContext
          key={`${path}:${buffer.base.entry.revision}`}
          source={{
            file: { ...buffer.base, entry: { ...buffer.base.entry, path } },
          }}
          threadId={threadId}
          selection={mode === "text" ? selection : undefined}
          compact
          disabled={
            buffer.dirty ||
            buffer.saving ||
            buffer.uncertain ||
            buffer.conflict ||
            read.isFetching ||
            !!read.error ||
            buffer.base.entry.size > 10 * 1024 * 1024
          }
        />
        {editable && mode === "text" && (
          <div className={styles.editorStatus}>
            {symlink ? (
              <small>{t("Read-only")}</small>
            ) : (
              buffer.dirty && <small>{t("Unsaved")}</small>
            )}
            {cursor && (
              <Button
                variant="ghost"
                size="sm"
                aria-label={t(
                  "Go to line, current line {{line}}, column {{column}}",
                  { line: cursor.line, column: cursor.column },
                )}
                title={t("Go to line ({{shortcut}} G)", {
                  shortcut: modifierLabel(),
                })}
                onClick={() => editor.current?.goToLine()}
              >
                {t("Ln {{line}}, Col {{column}}", {
                  line: cursor.line,
                  column: cursor.column,
                })}
                {cursor.selected > 0 &&
                  t(" - {{selected}} selected", {
                    selected: cursor.selected.toLocaleString(),
                  })}
              </Button>
            )}
            {!symlink && (
              <small className={styles.shortcuts}>
                <kbd className={shell.kbd}>{modifierLabel()} S</kbd> {t("Save")}
              </small>
            )}
          </div>
        )}
      </footer>
      <details className={styles.metadata}>
        <summary>{t("File details")}</summary>
        <p>
          {t("{{size}} bytes - {{type}}", {
            size: buffer.base.entry.size.toLocaleString(),
            type:
              media ?? (buffer.base.presentation ?? "binary").replace("_", " "),
          })}
        </p>
        <p>{path}</p>
        {buffer.base.resolved_path !== path && (
          <p>
            {t("Resolved path: {{path}}", { path: buffer.base.resolved_path })}
          </p>
        )}
        <DebugOnly>
          <p>
            {t("Server file - version {{revision}}", {
              revision: buffer.base.entry.revision,
            })}
          </p>
        </DebugOnly>
      </details>
    </section>
  );
}

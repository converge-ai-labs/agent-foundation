import { useContext, useEffect, useReducer, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import {
  ArrowClockwise,
  DownloadSimple,
  FloppyDisk,
  TextAlignLeft,
} from "@phosphor-icons/react";
import { ApiError, result } from "../transport/client";
import { useTransport } from "../transport/context";
import { SourceEditor } from "../configuration/editor";
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
  const transport = useTransport();
  const { client } = transport;
  const buffers = useContext(FileBuffers);
  const [, render] = useReducer((value: number) => value + 1, 0);
  const [error, setError] = useState<unknown>(null);
  const [message, setMessage] = useState("");
  const [wrap, setWrap] = useState(false);
  const [imageAttempt, setImageAttempt] = useState(0);
  const [mode, setMode] = useState<"preview" | "text">(() =>
    /\.(?:md|markdown)$/i.test(path) ? "preview" : "text",
  );
  const [selection, setSelection] = useState<LineRange>();
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
        {read.isPending && <p role="status">Reading file…</p>}
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
    <section className={styles.file} aria-label="File content">
      {symlink && (
        <div className={styles.notice}>
          Resolved target: {buffer.base.resolved_path}. This path is read-only;
          select the target explicitly to edit it.
          <Button
            variant="ghost"
            size="sm"
            onClick={() => open(buffer.base.resolved_path)}
          >
            Open target
          </Button>
        </div>
      )}
      <div className={styles.fileToolbar}>
        <div className={styles.actions} aria-label="File actions">
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
              Save
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Refresh"
            title="Refresh file"
            onClick={() => void refreshContent()}
            disabled={buffer.saving || read.isFetching}
          >
            <ArrowClockwise />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Download"
            title="Download file"
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
              Download draft
            </Button>
          )}
        </div>
        {editable && (
          <div className={styles.viewControls} aria-label="File view">
            {markdown && (
              <>
                <Button
                  variant={mode === "preview" ? "secondary" : "ghost"}
                  size="sm"
                  aria-pressed={mode === "preview"}
                  onClick={() => setMode("preview")}
                >
                  Preview
                </Button>
                <Button
                  variant={mode === "text" ? "secondary" : "ghost"}
                  size="sm"
                  aria-pressed={mode === "text"}
                  onClick={() => setMode("text")}
                >
                  Text
                </Button>
              </>
            )}
            {mode === "text" && (
              <Button
                size="sm"
                variant="ghost"
                aria-pressed={wrap}
                onClick={() => setWrap(!wrap)}
              >
                <TextAlignLeft />
                Wrap
              </Button>
            )}
          </div>
        )}
      </div>
      <ErrorNotice
        error={error || read.error}
        retry={() => void read.refetch()}
      />
      {message && <p role="status">{message}</p>}
      {(buffer.conflict || buffer.uncertain) && (
        <div className={styles.notice}>
          <strong>
            {buffer.uncertain
              ? "Save outcome needs inspection"
              : "Disk revision changed"}
          </strong>
          <p>
            Your edits are kept. Refresh and review the saved file before
            choosing which version to keep.
          </p>
          <details>
            <summary>Show latest saved version</summary>
            <p>
              Revision: {buffer.observed.entry.revision} ·{" "}
              {buffer.observed.presentation}
            </p>
            {buffer.observed.text != null && (
              <SourceEditor
                value={buffer.observed.text}
                language="plain"
                filename={path}
                readOnly
                label="Latest disk text"
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
                  Use disk version
                </Button>
              }
              title="Replace your local text?"
              description="Replace your edits with this saved version? Download your edits first if you need a copy."
              confirmLabel="Use disk version"
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
                  Keep local text
                </Button>
              }
              title="Keep your local text?"
              description="Keep your edits? Your next save will replace the file version you just reviewed."
              confirmLabel="Keep local text"
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
          This file mixes line endings. Editing uses the first style throughout.
          Viewing or attaching leaves the file unchanged.
        </p>
      )}
      {editable ? (
        mode === "preview" && markdown ? (
          <article
            className={styles.markdownPreview}
            aria-label="Markdown preview"
          >
            <MessageText text={buffer.value} />
          </article>
        ) : (
          <div className={styles.editor}>
            <SourceEditor
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
              label={`File text: ${path}`}
              fill
              readOnly={symlink}
              onSelection={setSelection}
              onChange={(value) => {
                buffer.value = value;
                render();
                onBufferChange?.();
              }}
            />
          </div>
        )
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
            <h3>Image exceeds the preview limit</h3>
            <p>
              Image too large to preview. Download it to view. The preview limit
              is 10 MiB.
            </p>
          </div>
        )
      ) : (
        <div className={styles.empty}>
          <h3>
            {buffer.base.presentation === "binary"
              ? "Binary file"
              : "File exceeds the text editor limit"}
          </h3>
          <p>
            Edit UTF-8 text files up to 512 KiB, without NUL characters. Upload
            or attach files up to 10 MiB. Download larger files to view them.
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
          <small className={styles.shortcuts}>
            <kbd className={shell.kbd}>{modifierLabel()} G</kbd> Go to line
            <kbd className={shell.kbd}>{modifierLabel()} S</kbd> Save
          </small>
        )}
      </footer>
      <details className={styles.metadata}>
        <summary>File details</summary>
        <p>
          {buffer.base.entry.size.toLocaleString()} bytes ·{" "}
          {media ?? (buffer.base.presentation ?? "binary").replace("_", " ")}
        </p>
        <p>{path}</p>
        {buffer.base.resolved_path !== path && (
          <p>Resolved path: {buffer.base.resolved_path}</p>
        )}
        <DebugOnly>
          <p>Server file · version {buffer.base.entry.revision}</p>
        </DebugOnly>
      </details>
    </section>
  );
}

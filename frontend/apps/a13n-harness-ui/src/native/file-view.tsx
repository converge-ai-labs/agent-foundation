import { useContext, useEffect, useReducer, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { ApiError, result } from "../transport/client";
import { useTransport } from "../transport/context";
import { SourceEditor } from "../configuration/editor";
import { ErrorNotice } from "../shell/ui";
import { ConfirmAction } from "../shell/confirm-action";
import {
  basename,
  FileBuffer,
  FileBuffers,
  mixedLineEndings,
  type LineRange,
} from "./buffer";
import { CaptureContext, downloadBlob } from "./capture";
import styles from "./native.module.css";

export function FileView({
  path,
  threadId,
  refresh,
  open,
}: {
  path: string;
  threadId?: string;
  refresh: () => void;
  open: (path: string) => void;
}) {
  const { client, fetch } = useTransport();
  const buffers = useContext(FileBuffers);
  const [, render] = useReducer((value: number) => value + 1, 0);
  const [error, setError] = useState<unknown>(null);
  const [message, setMessage] = useState("");
  const [selection, setSelection] = useState<LineRange>();
  const read = useQuery({
    queryKey: ["native", "text", path],
    queryFn: async ({ signal }) => {
      const next = await result(
        client.GET("/api/host/files/text", {
          params: { query: { path } },
          signal,
        }),
      );
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
    if (buffer.saving || buffer.uncertain || buffer.conflict || symlink) return;
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
          "Save acknowledgement lost. Do not repeat the write; refresh and inspect disk before choosing which text to keep.",
        );
    } finally {
      buffer.saving = false;
      render();
      refresh();
    }
  };
  const download = async () => {
    setError(null);
    try {
      const query = new URLSearchParams({
        path,
        expected_revision: buffer.base.entry.revision,
      });
      const response = await fetch(`/api/host/files/content?${query}`);
      downloadBlob(await response.blob(), basename(path));
    } catch (failure) {
      setError(failure);
    }
  };
  const editable = buffer.base.presentation === "text";
  return (
    <section className={styles.file} aria-label="File content">
      <header className={styles.fileHeader}>
        <strong>
          {basename(path)}
          {buffer.dirty ? " · Unsaved" : ""}
        </strong>
        <span>
          {buffer.base.entry.size.toLocaleString()} bytes ·{" "}
          {buffer.base.presentation.replace("_", " ")}
        </span>
      </header>
      <div className={styles.path}>{path}</div>
      {symlink && (
        <div className={styles.notice}>
          Resolved target: {buffer.base.resolved_path}. This path is read-only;
          select the target explicitly to edit it.
          <Button
            variant="ghost"
            size="sm"
            onClick={() => open(buffer.base.resolved_path)}
          >
            Open resolved target
          </Button>
        </div>
      )}
      <div className={styles.actions}>
        {editable && (
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
            Save file
          </Button>
        )}
        <Button
          variant="ghost"
          size="sm"
          onClick={() => void read.refetch()}
          disabled={buffer.saving}
        >
          Refresh disk
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => void download()}
          disabled={buffer.base.entry.size > 10 * 1024 * 1024}
        >
          Download disk file
        </Button>
        {editable && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() =>
              downloadBlob(
                new Blob([buffer.value], { type: "text/plain;charset=utf-8" }),
                basename(path),
              )
            }
          >
            Download local text
          </Button>
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
            Your local text has not been replaced. Refresh, then review the
            current disk version below before resolving.
          </p>
          <details>
            <summary>Inspect latest disk version</summary>
            <p>
              Revision: {buffer.observed.entry.revision} ·{" "}
              {buffer.observed.presentation}
            </p>
            {buffer.observed.text != null && (
              <SourceEditor
                value={buffer.observed.text}
                language="plain"
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
              description="Replace your local text with this inspected disk version? Download local text first if you need a copy."
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
              description="Keep your local text and use the inspected disk revision for the next explicit save? That save will replace this disk content."
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
          This file has mixed line endings. Editing normalizes them to its first
          line-ending style; opening or capturing does not change its bytes.
        </p>
      )}
      {editable ? (
        <div className={styles.editor}>
          <SourceEditor
            value={buffer.value}
            language="plain"
            label={`File text: ${path}`}
            fill
            readOnly={symlink}
            onSelection={setSelection}
            onChange={(value) => {
              buffer.value = value;
              render();
            }}
          />
        </div>
      ) : (
        <div className={styles.empty}>
          <h3>
            {buffer.base.presentation === "binary"
              ? "Binary file"
              : "File exceeds the text editor limit"}
          </h3>
          <p>
            Only complete, NUL-free UTF-8 up to 512 KiB is editable here.
            Downloads and whole-file captures support up to 10 MiB; larger files
            need another native workflow.
          </p>
        </div>
      )}
      <CaptureContext
        key={`${path}:${buffer.base.entry.revision}`}
        source={{
          file: { ...buffer.base, entry: { ...buffer.base.entry, path } },
        }}
        threadId={threadId}
        selection={selection}
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
      <details className={styles.metadata}>
        <summary>Reviewed file identity</summary>
        <p>Server native filesystem · revision {buffer.base.entry.revision}</p>
        <p>Resolved path: {buffer.base.resolved_path}</p>
      </details>
    </section>
  );
}

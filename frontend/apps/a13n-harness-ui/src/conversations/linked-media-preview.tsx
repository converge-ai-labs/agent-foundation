import { useEffect, useRef, useState } from "react";
import { result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import { ErrorNotice } from "../shell/ui";
import { FileMedia } from "../native/file-media";
import { mediaKind, MAX_MEDIA_BYTES } from "../native/media-kind";
import styles from "./markdown.module.css";

export function LinkedMediaPreview({ path }: { path: string }) {
  const transport = useTransport();
  const container = useRef<HTMLElement>(null);
  const [visible, setVisible] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [read, setRead] = useState<{
    path: string;
    attempt: number;
    file?: Schema<"FileInfo">;
    error?: unknown;
  }>();
  useEffect(() => {
    if (!container.current || typeof IntersectionObserver === "undefined") {
      setVisible(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setVisible(true);
          observer.disconnect();
        }
      },
      { rootMargin: "240px" },
    );
    observer.observe(container.current);
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    if (!visible) return;
    const controller = new AbortController();
    setRead(undefined);
    void result(
      transport.client.GET("/api/host/files/info", {
        params: { query: { path } },
        signal: controller.signal,
      }),
    ).then(
      (file) => {
        if (!controller.signal.aborted) setRead({ path, attempt, file });
      },
      (error: unknown) => {
        if (!controller.signal.aborted) setRead({ path, attempt, error });
      },
    );
    return () => controller.abort();
  }, [transport, path, visible, attempt]);
  const current =
    read?.path === path && read.attempt === attempt ? read : undefined;
  const file = current?.file;
  const kind = mediaKind(file?.media_type);
  if (file && !kind) return null;
  return (
    <figure
      ref={container}
      className={styles.linkedPreview}
      data-state={!current ? "loading" : current.error ? "error" : "ready"}
      aria-label={`Media preview: ${path.split(/[\\/]/).at(-1)}`}
    >
      {!current && (
        <p role="status" className={styles.srOnly}>
          Loading media preview…
        </p>
      )}
      <ErrorNotice
        inline
        error={current?.error}
        retry={() => setAttempt((value) => value + 1)}
      />
      {file &&
        (kind === "image" && file.entry.size > MAX_MEDIA_BYTES ? (
          <p>
            Too large to preview. Open the original file. The preview limit is
            10 MiB.
          </p>
        ) : !kind ? (
          <p>Can't preview this file. Open the original instead.</p>
        ) : (
          <FileMedia
            key={`${path}:${file.entry.revision}:${attempt}`}
            path={path}
            revision={file.entry.revision}
            kind={kind}
            retry={() => setAttempt((value) => value + 1)}
          />
        ))}
    </figure>
  );
}

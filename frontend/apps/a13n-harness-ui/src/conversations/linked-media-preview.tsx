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
    file?: Schema<"FileText">;
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
      transport.client.GET("/api/host/files/text", {
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
  const kind = mediaKind(path);
  return (
    <figure
      ref={container}
      className={styles.linkedPreview}
      aria-label={`Media preview: ${path.split(/[\\/]/).at(-1)}`}
    >
      {!current && <p role="status">Loading media preview…</p>}
      <ErrorNotice
        error={current?.error}
        retry={() => setAttempt((value) => value + 1)}
      />
      {file &&
        (file.entry.size > MAX_MEDIA_BYTES ? (
          <p>
            Preview supports files up to 10 MiB. Open the original file to
            inspect it.
          </p>
        ) : file.presentation === "text" || !kind ? (
          <p>
            This file cannot be previewed as media. Open the original file to
            inspect it.
          </p>
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

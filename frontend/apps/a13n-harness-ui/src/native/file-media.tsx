import { useEffect, useState } from "react";
import { ArrowsOut } from "@phosphor-icons/react";
import { useTransport } from "../transport/context";
import { ImagePreview } from "../shell/image-preview";
import { ErrorNotice } from "../shell/ui";
import { basename } from "./buffer";
import type { MediaKind } from "./media-kind";
import styles from "./file-image.module.css";

/** The parent keys this view by requested path, reviewed revision and refresh. */
export function FileMedia({
  path,
  revision,
  retry,
  kind,
}: {
  path: string;
  revision: string;
  retry: () => void;
  kind: MediaKind;
}) {
  const transport = useTransport();
  const [src, setSrc] = useState<string>();
  const [error, setError] = useState<unknown>();
  const [dimensions, setDimensions] = useState<string>();
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let url: string | undefined;
    const query = new URLSearchParams({ path, expected_revision: revision });
    void transport
      .fetch(`/api/host/files/content?${query}`, { signal: controller.signal })
      .then((response) => response.blob())
      .then((blob) => {
        if (controller.signal.aborted) return;
        url = URL.createObjectURL(blob);
        setSrc(url);
      })
      .catch((failure: unknown) => {
        if (!controller.signal.aborted) setError(failure);
      });
    return () => {
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [transport, path, revision]);
  const name = basename(path);
  const label = `${kind[0].toUpperCase()}${kind.slice(1)}`;
  const decodeError = () =>
    setError(
      new Error(
        `This ${kind} cannot be previewed. Download the original to inspect it.`,
      ),
    );
  return (
    <section className={styles.preview} aria-label={`${label} preview`}>
      {(!src || (kind === "image" && !dimensions)) && !error && (
        <p role="status">Loading {kind}…</p>
      )}
      <ErrorNotice error={error} retry={retry} />
      {src && !error && (
        <>
          {kind === "image" ? (
            <button
              type="button"
              className={styles.canvas}
              aria-label={`Expand image: ${name}`}
              disabled={!dimensions}
              onClick={() => setExpanded(true)}
            >
              <img
                src={src}
                alt={name}
                hidden={!dimensions}
                onLoad={(event) => {
                  const image = event.currentTarget;
                  setDimensions(
                    `${image.naturalWidth} × ${image.naturalHeight}`,
                  );
                }}
                onError={decodeError}
              />
            </button>
          ) : kind === "audio" ? (
            <audio
              src={src}
              controls
              preload="metadata"
              aria-label={`Audio preview: ${name}`}
              onError={decodeError}
            />
          ) : (
            <video
              src={src}
              controls
              preload="metadata"
              playsInline
              aria-label={`Video preview: ${name}`}
              onError={decodeError}
            />
          )}
          {dimensions && (
            <div className={styles.caption}>
              <span>{dimensions}</span>
              <span>
                <ArrowsOut aria-hidden="true" /> Click image to expand
              </span>
            </div>
          )}
          {expanded && (
            <ImagePreview
              src={src}
              name={name}
              close={() => setExpanded(false)}
            />
          )}
        </>
      )}
    </section>
  );
}

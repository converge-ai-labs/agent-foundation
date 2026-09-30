import { useEffect, useRef, useState } from "react";
import { ArrowsOut } from "@phosphor-icons/react";
import { useTransport } from "../transport/context";
import { ApiError } from "../transport/client";
import { ImagePreview } from "../shell/image-preview";
import { ErrorNotice } from "../shell/ui";
import { basename } from "./buffer";
import type { MediaKind } from "./media-kind";
import { fileTransfer } from "./file-transfer";
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
  const [ready, setReady] = useState(false);
  const player = useRef<HTMLMediaElement>(null);
  const image = useRef<HTMLImageElement>(null);
  const lifetime = useRef<AbortController | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    lifetime.current = controller;
    void fileTransfer(transport, path, revision, "inline", controller.signal)
      .then((access) => {
        if (!controller.signal.aborted) setSrc(access.url);
      })
      .catch((failure: unknown) => {
        if (!controller.signal.aborted) setError(failure);
      });
    return () => {
      controller.abort();
    };
  }, [transport, path, revision, kind]);
  useEffect(() => {
    const element = player.current;
    const picture = image.current;
    return () => {
      picture?.removeAttribute("src");
      if (element) {
        element.pause();
        element.removeAttribute("src");
        element.load();
      }
    };
  }, [src, error]);
  const name = basename(path);
  const label = `${kind[0].toUpperCase()}${kind.slice(1)}`;
  const decodeError = () => {
    const failure = new Error(
      `This ${kind} cannot be previewed. The browser may not support its codec. Download the original to inspect it.`,
    );
    if (!src) {
      setError(failure);
      return;
    }
    // Native elements hide HTTP failures; HEAD has no JSON error body.
    // Interpret the transfer status without downloading bytes or retrying playback.
    const signal = lifetime.current?.signal;
    void transport.fetch(src, { method: "HEAD", signal }).then(
      () => {
        if (!signal?.aborted) setError(failure);
      },
      (error: unknown) => {
        if (signal?.aborted) return;
        if (error instanceof ApiError && error.status === 409)
          setError(
            new Error(
              "File content changed. Retry to review the current file.",
            ),
          );
        else if (error instanceof ApiError && error.status === 403)
          setError(
            new Error(
              "File access expired or is unavailable. Retry to obtain new access.",
            ),
          );
        else setError(error);
      },
    );
  };
  return (
    <section className={styles.preview} aria-label={`${label} preview`}>
      {(!src || (kind === "image" ? !dimensions : !ready)) && !error && (
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
                ref={image}
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
              ref={(element) => {
                player.current = element;
              }}
              src={src}
              controls
              preload="metadata"
              aria-label={`Audio preview: ${name}`}
              onError={decodeError}
              onLoadedMetadata={() => setReady(true)}
            />
          ) : (
            <video
              ref={(element) => {
                player.current = element;
              }}
              src={src}
              controls
              preload="metadata"
              playsInline
              aria-label={`Video preview: ${name}`}
              onError={decodeError}
              onLoadedMetadata={() => setReady(true)}
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

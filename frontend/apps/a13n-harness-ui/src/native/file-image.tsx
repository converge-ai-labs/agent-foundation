import { useEffect, useState } from "react";
import { ArrowsOut } from "@phosphor-icons/react";
import { useTransport } from "../transport/context";
import { ImagePreview } from "../shell/image-preview";
import { ErrorNotice } from "../shell/ui";
import { basename } from "./buffer";
import styles from "./file-image.module.css";

// Extensions select preview candidates, not trusted MIME types. Bytes are only
// decoded in an img, never embedded as an active document.
export const isImagePath = (path: string) =>
  /\.(?:png|jpe?g|webp|gif)$/i.test(path);

/** The parent keys this view by requested path, reviewed revision and refresh. */
export function FileImage({
  path,
  revision,
  retry,
}: {
  path: string;
  revision: string;
  retry: () => void;
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
  return (
    <section className={styles.preview} aria-label="Image preview">
      {!dimensions && !error && <p role="status">Loading image…</p>}
      <ErrorNotice error={error} retry={retry} />
      {src && !error && (
        <>
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
                setDimensions(`${image.naturalWidth} × ${image.naturalHeight}`);
              }}
              onError={() =>
                setError(
                  new Error(
                    "This image cannot be previewed. Download the original to inspect it.",
                  ),
                )
              }
            />
          </button>
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

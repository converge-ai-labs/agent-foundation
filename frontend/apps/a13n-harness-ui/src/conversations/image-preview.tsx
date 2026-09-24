import { useEffect, useState } from "react";
import type { Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import { ImagePreview } from "../shell/image-preview";
import { attachmentPath } from "./attachment-thumbnail";

export function RetainedImagePreview({
  threadId,
  attachment,
  close,
}: {
  threadId: string;
  attachment: Schema<"ThreadAttachment">;
  close: () => void;
}) {
  const transport = useTransport();
  const [src, setSrc] = useState<string>();
  const [error, setError] = useState<unknown>();
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let url: string | undefined;
    setSrc(undefined);
    setError(undefined);
    void transport
      .fetch(attachmentPath(threadId, attachment.attachment_id), {
        signal: controller.signal,
      })
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
  }, [transport, threadId, attachment.attachment_id, attempt]);
  return (
    <ImagePreview
      src={src}
      name={attachment.name}
      close={close}
      loading={!src && !error}
      error={error}
      retry={() => setAttempt((value) => value + 1)}
    />
  );
}

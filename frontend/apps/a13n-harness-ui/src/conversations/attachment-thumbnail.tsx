import { useEffect, useState } from "react";
import type { Schema, Transport } from "../transport/client";
import { useTransport } from "../transport/context";
import styles from "./conversation.module.css";

export const attachmentPath = (threadId: string, id: string) =>
  `/api/threads/${encodeURIComponent(threadId)}/attachments/${encodeURIComponent(id)}`;

// Both editor widgets and transcript thumbnails use the authenticated retained
// bytes endpoint. Never load media URLs or current server filesystem paths.
export function retainedImage(
  image: HTMLImageElement,
  transport: Transport,
  threadId: string,
  attachment: Schema<"ThreadAttachment">,
) {
  const controller = new AbortController();
  let url: string | undefined;
  image.alt = attachment.name;
  image.className = styles.attachmentThumbnail;
  image.hidden = true;
  image.onload = () => {
    image.hidden = false;
  };
  image.onerror = () => {
    image.hidden = true;
  };
  if (attachment.media_type.startsWith("image/"))
    void transport
      .fetch(attachmentPath(threadId, attachment.attachment_id), {
        signal: controller.signal,
      })
      .then((response) => response.blob())
      .then((blob) => {
        if (controller.signal.aborted) return;
        url = URL.createObjectURL(blob);
        image.src = url;
      })
      .catch(() => {
        /* The filename and explicit download remain available. */
      });
  return () => {
    controller.abort();
    image.onload = null;
    image.onerror = null;
    image.removeAttribute("src");
    if (url) URL.revokeObjectURL(url);
  };
}

export function AttachmentThumbnail({
  threadId,
  attachment,
}: {
  threadId: string;
  attachment: Schema<"ThreadAttachment">;
}) {
  const transport = useTransport();
  const [image, setImage] = useState<HTMLImageElement | null>(null);
  useEffect(() => {
    if (image) return retainedImage(image, transport, threadId, attachment);
    // Metadata objects are replaced at live/history cutover; retained bytes are not.
  }, [
    image,
    transport,
    threadId,
    attachment.attachment_id,
    attachment.media_type,
    attachment.name,
  ]);
  if (!attachment.media_type.startsWith("image/")) return null;
  return (
    <img
      ref={setImage}
      alt={attachment.name}
      className={styles.attachmentThumbnail}
      hidden
    />
  );
}

export type MediaKind = "image" | "audio" | "video";
export const MAX_MEDIA_BYTES = 10 * 1024 * 1024;

// MIME is a server filename hint. Browser decoding decides actual support;
// active documents stay in the text/download presentation, never an embed.
export function mediaKind(mediaType: string | undefined): MediaKind | null {
  if (mediaType?.startsWith("image/") && mediaType !== "image/svg+xml")
    return "image";
  if (mediaType?.startsWith("audio/")) return "audio";
  if (mediaType?.startsWith("video/")) return "video";
  return null;
}

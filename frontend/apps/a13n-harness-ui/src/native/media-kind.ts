export type MediaKind = "image" | "audio" | "video";
export const MAX_MEDIA_BYTES = 10 * 1024 * 1024;

// Candidate formats, not trusted MIME types. Only passive browser decoders see
// these bytes; HTML, SVG and other active documents are never embedded.
export function mediaKind(path: string): MediaKind | null {
  if (/\.(?:png|jpe?g|webp|gif)$/i.test(path)) return "image";
  if (/\.(?:mp3|m4a|wav|ogg|opus|flac|aac)$/i.test(path)) return "audio";
  if (/\.(?:mp4|webm|mov|m4v)$/i.test(path)) return "video";
  return null;
}

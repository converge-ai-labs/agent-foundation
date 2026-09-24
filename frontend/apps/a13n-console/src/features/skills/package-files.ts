import { unzipSync } from "fflate";
import type { Schema } from "../../shared/api";

export const previewLimit = 1024 * 1024;

/** Manifest paths are below `root`, the archive directory that holds SKILL.md. */
export function readTextFile(
  archive: Uint8Array,
  root: string,
  file: Schema["SkillFile"],
): string | null {
  if (file.size > previewLimit) return null;
  const name = root + file.path;
  const bytes = unzipSync(archive, {
    filter: (entry) =>
      entry.name === name && entry.originalSize <= previewLimit,
  })[name];
  if (!bytes || bytes.length !== file.size)
    throw new Error("The package file does not match its manifest.");
  if (bytes.includes(0)) return null;
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return null;
  }
}

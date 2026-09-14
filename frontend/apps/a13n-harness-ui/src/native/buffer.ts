import { createContext } from "react";
import type { Schema } from "../transport/client";

/** A private edit buffer, never a CRDT or a claim that disk stayed unchanged. */
export class FileBuffer {
  value: string;
  observed: Schema<"FileText">;
  saving = false;
  uncertain = false;
  constructor(public base: Schema<"FileText">) {
    this.observed = base;
    this.value = base.text ?? "";
  }
  get dirty() {
    return this.value !== (this.base.text ?? "");
  }
  get conflict() {
    return this.observed.entry.revision !== this.base.entry.revision;
  }
  observe(next: Schema<"FileText">) {
    this.observed = next;
    if (!this.dirty && !this.saving && !this.uncertain) {
      this.base = next;
      this.value = next.text ?? "";
    }
  }
  saved(entry: Schema<"FileEntry">, submitted: string) {
    this.base = {
      entry,
      resolved_path: entry.path,
      presentation: "text",
      text: submitted,
    };
    this.observed = this.base;
    this.uncertain = false;
    // Typing during the request is not part of the acknowledged save.
  }
  adopt(keepText: boolean) {
    this.base = this.observed;
    if (!keepText) this.value = this.base.text ?? "";
    this.uncertain = false;
  }
}
export const FileBuffers = createContext(new Map<string, FileBuffer>());

// Native paths are not browser URLs. Preserve Windows drive/UNC roots and POSIX
// backslashes (which are legal filename characters); never expand a shell path.
export function separator(path: string) {
  return /^(?:[A-Za-z]:\\|\\\\)/.test(path) ? "\\" : "/";
}
export function basename(path: string) {
  const sep = separator(path);
  return (
    path
      .replace(new RegExp(sep === "\\" ? "\\\\+$" : "/+$"), "")
      .split(sep)
      .at(-1) || path
  );
}
export function parentPath(path: string) {
  const sep = separator(path);
  const root =
    sep === "/"
      ? "/"
      : path.match(/^(?:[A-Za-z]:\\|\\\\[^\\]+\\[^\\]+\\?)/)?.[0];
  if (!root) return path;
  const clean = path.endsWith(sep) ? path.slice(0, -1) : path;
  if (clean.length <= root.replace(/\\$/, "").length) return root;
  const end = clean.lastIndexOf(sep);
  return end < root.length ? root : clean.slice(0, end);
}
export function joinPath(directory: string, name: string) {
  const sep = separator(directory);
  return `${directory}${directory.endsWith(sep) ? "" : sep}${name}`;
}
export function breadcrumbs(path: string): string[] {
  const result = [path];
  while (result.length < 128) {
    const parent = parentPath(result[0]!);
    if (parent === result[0]) break;
    result.unshift(parent);
  }
  return result;
}
// Python's capture API recognizes additional separators that CodeMirror does not.
// Whole-source capture remains exact; never guess a range on these documents.
export function supportsLineRanges(text: string) {
  return !/[\v\f\x1c-\x1e\u0085\u2028\u2029]/.test(text);
}
export function mixedLineEndings(text: string) {
  return new Set(text.match(/\r\n|\r|\n/g)).size > 1;
}
export type LineRange = { start_line: number; end_line: number };
export function lineRange(start: string, end: string): LineRange {
  const first = Number(start),
    last = Number(end);
  if (
    !Number.isSafeInteger(first) ||
    !Number.isSafeInteger(last) ||
    first < 1 ||
    last < first
  )
    throw new Error(
      "Choose both one-based line numbers, with the end at or after the start.",
    );
  return { start_line: first, end_line: last };
}

export function gitPath(root: string, relative: string) {
  return relative.split("/").reduce(joinPath, root);
}

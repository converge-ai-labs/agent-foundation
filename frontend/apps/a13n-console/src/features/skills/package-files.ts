import { unzipSync } from "fflate";
import type { Schema } from "../../shared/api";

export const previewLimit = 1024 * 1024;

export type FileNode = {
  name: string;
  path: string;
  children?: FileNode[];
};

export function fileTree(files: Schema["SkillPackageFile"][]): FileNode[] {
  const root: FileNode[] = [];
  for (const file of files) {
    let siblings = root;
    const segments = file.path.split("/");
    segments.forEach((name, index) => {
      const directory = index < segments.length - 1;
      let node = siblings.find((item) => item.name === name);
      if (!node) {
        node = {
          name,
          path: segments.slice(0, index + 1).join("/"),
          ...(directory && { children: [] }),
        };
        siblings.push(node);
      }
      if (node.children) siblings = node.children;
    });
  }
  function sort(nodes: FileNode[]) {
    nodes.sort(
      (a, b) =>
        Number(!!b.children) - Number(!!a.children) ||
        a.name.localeCompare(b.name),
    );
    nodes.forEach((node) => {
      if (node.children) sort(node.children);
    });
  }
  sort(root);
  return root;
}

export function readTextFile(
  archive: Uint8Array,
  file: Schema["SkillPackageFile"],
): string | null {
  if (file.size_bytes > previewLimit) return null;
  const bytes = unzipSync(archive, {
    filter: (entry) =>
      entry.name === file.path && entry.originalSize <= previewLimit,
  })[file.path];
  if (!bytes || bytes.length !== file.size_bytes)
    throw new Error("The package file does not match its manifest.");
  if (bytes.includes(0)) return null;
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return null;
  }
}

export function markdownBody(text: string): string {
  return text.replace(/^\uFEFF?---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/, "");
}

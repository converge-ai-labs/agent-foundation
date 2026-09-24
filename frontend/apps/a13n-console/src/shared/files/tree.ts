export type FileNode = {
  name: string;
  path: string;
  children?: FileNode[];
};

/** Directories are implicit in the paths; they list before files, by name. */
export function fileTree(files: readonly { path: string }[]): FileNode[] {
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

export function isMarkdown(path: string): boolean {
  return /\.md$/i.test(path);
}

/** A preview renders Markdown without its leading frontmatter block. */
export function markdownBody(text: string): string {
  return text.replace(/^﻿?---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/, "");
}

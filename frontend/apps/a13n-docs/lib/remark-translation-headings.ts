import { readFile } from "node:fs/promises";
import type { Heading, Root, RootContent } from "mdast";
import { fromMarkdown } from "mdast-util-from-markdown";
import Slugger from "github-slugger";
import { visit } from "unist-util-visit";

function text(node: RootContent): string {
  if ("children" in node)
    return node.children.map((child) => text(child as RootContent)).join("");
  return "value" in node ? node.value : "";
}

export function headingIds(markdown: string) {
  // Front matter must not be mistaken for a Setext heading.
  const root = fromMarkdown(
    markdown.replace(/^---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/, ""),
  );
  const slugger = new Slugger();
  const headings: { depth: Heading["depth"]; id: string }[] = [];
  visit(root, "heading", (heading) => {
    const label = text(heading);
    const custom = /\s*\[#([^]+?)]\s*$/.exec(label);
    headings.push({
      depth: heading.depth,
      id: custom?.[1] ?? slugger.slug(label),
    });
  });
  return headings;
}

/** Keep section links and language switches stable while translating heading labels. */
export function remarkTranslationHeadings() {
  return async (root: Root, file: { path: string }) => {
    if (!/\.zh-CN\.mdx?$/.test(file.path)) return;
    const english = await readFile(
      file.path.replace(/\.zh-CN(?=\.mdx?$)/, ""),
      "utf8",
    );
    const originals = headingIds(english);
    let index = 0;
    visit(root, "heading", (heading) => {
      const original = originals[index++];
      if (!original || original.depth !== heading.depth)
        throw new Error(`Translation heading structure differs: ${file.path}`);
      heading.data ??= {};
      heading.data.hProperties = {
        ...heading.data.hProperties,
        id: original.id,
      };
    });
    if (index !== originals.length)
      throw new Error(`Translation is missing headings: ${file.path}`);
  };
}

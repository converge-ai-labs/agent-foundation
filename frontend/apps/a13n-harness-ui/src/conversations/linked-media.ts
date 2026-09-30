import { linkedHostFile } from "../shell/page-links";
import { mediaKind } from "../native/media-kind";

type MediaNode = {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: MediaNode[];
};

// Add block previews next to prose, never interactive controls inside anchors
// or paragraphs. Original links stay intact. Remote URLs remain ordinary links.
export function linkedMediaPreviews({ currentHref }: { currentHref: string }) {
  return (tree: MediaNode) => {
    const seen = new Set<string>();
    const collect = (
      node: MediaNode,
      paths: Set<string>,
      insideAnchor = false,
    ) => {
      if (
        !node.tagName ||
        !["a", "img", "strong", "em", "del", "span"].includes(node.tagName)
      )
        return;
      const href = node.properties?.[node.tagName === "img" ? "src" : "href"];
      const path =
        typeof href === "string" ? linkedHostFile(href, currentHref) : null;
      if (path && mediaKind(path)) {
        if (!seen.has(path) && seen.size < 8) {
          seen.add(path);
          paths.add(path);
        }
        if (node.tagName === "img") {
          const name = String(
            node.properties?.alt || path.split(/[\\/]/).at(-1),
          );
          node.tagName = insideAnchor ? "span" : "a";
          node.properties = insideAnchor ? {} : { href };
          node.children = [{ type: "text", value: name }];
        }
      }
      node.children?.forEach((child) =>
        collect(child, paths, insideAnchor || node.tagName === "a"),
      );
    };
    const previews = (paths: Set<string>): MediaNode[] =>
      [...paths].map((path) => ({
        type: "element",
        tagName: "figure",
        properties: { dataHostMediaPath: path },
        children: [],
      }));
    const visit = (parent: MediaNode) => {
      if (!parent.children) return;
      const next: MediaNode[] = [];
      for (const child of parent.children) {
        const paths = new Set<string>();
        if (child.tagName === "p") {
          child.children?.forEach((inline) => collect(inline, paths));
          next.push(child, ...previews(paths));
        } else {
          if (["li", "td", "th"].includes(child.tagName ?? ""))
            child.children?.forEach((inline) => collect(inline, paths));
          visit(child);
          if (paths.size) child.children?.push(...previews(paths));
          next.push(child);
        }
      }
      parent.children = next;
    };
    visit(tree);
  };
}

import type { Blockquote, Paragraph, Root } from "mdast";
import type {} from "mdast-util-mdx-jsx";
import { visit } from "unist-util-visit";

const MARKER = /^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*/;

/**
 * Render GitHub alerts (`> [!NOTE]`) as callouts, so Markdown sources stay
 * readable on GitHub and in bundled skills while the site shows a callout.
 */
export function remarkAlerts() {
  return (tree: Root) => {
    visit(tree, "blockquote", (node: Blockquote, index, parent) => {
      const first = node.children[0];
      if (!parent || index === undefined || first?.type !== "paragraph") return;
      const text = first.children[0];
      if (text?.type !== "text") return;
      const match = MARKER.exec(text.value);
      if (!match) return;

      text.value = text.value.slice(match[0].length);
      if (text.value === "") first.children.shift();
      const children = (
        first.children.length === 0 ? node.children.slice(1) : node.children
      ) as Paragraph[];
      parent.children[index] = {
        type: "mdxJsxFlowElement",
        name: "Callout",
        attributes: [
          {
            type: "mdxJsxAttribute",
            name: "type",
            value: match[1].toLowerCase(),
          },
        ],
        children,
      };
    });
  };
}

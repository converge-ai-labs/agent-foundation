import type { Root } from "mdast";
import type {} from "mdast-util-mdx-jsx";
import { visit } from "unist-util-visit";

const OPEN_ANCHOR = /^<span\s+id=(["'])([A-Za-z][A-Za-z0-9_.:-]*)\1\s*>$/;

/** Keep empty compatibility anchors in Markdown without enabling raw HTML. */
export function remarkExplicitAnchors() {
  return (tree: Root) => {
    visit(tree, "html", (node, index, parent) => {
      if (!parent || index === undefined) return;
      const match = OPEN_ANCHOR.exec(node.value);
      const close = parent.children[index + 1];
      if (!match || close?.type !== "html" || close.value !== "</span>") return;

      // Markdown parses an empty inline span as adjacent opening/closing nodes.
      // Requiring adjacency excludes content, expressions, and other attributes.
      parent.children.splice(index, 2, {
        type: "mdxJsxTextElement",
        name: "span",
        attributes: [{ type: "mdxJsxAttribute", name: "id", value: match[2] }],
        children: [],
      });
      return index + 1;
    });
  };
}

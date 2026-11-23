import type { Extension } from "mdast-util-from-markdown";
import type { Processor } from "unified";
import type {} from "remark-parse";

export { remarkLatex } from "./remark-latex";

const closedDiagram: Extension = {
  enter: {
    codeFencedFence() {
      // The parser buffers code in a fragment above the code node.
      const node = this.stack.at(-2);
      // The second fence token exists only for an actual CommonMark closing
      // fence, including inside lists and block quotes. An EOF code block
      // alone is not enough to render a diagram in a growing reply.
      if (
        node?.type === "code" &&
        node.lang?.toLowerCase() === "mermaid" &&
        this.data.flowCodeInside === true
      ) {
        node.data = { ...node.data, hProperties: { dataClosedDiagram: true } };
      }
    },
  },
};

export function remarkClosedDiagram(this: Processor) {
  (this.data().fromMarkdownExtensions ??= []).push(closedDiagram);
}

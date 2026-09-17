import { memo, useMemo } from "react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { sourceAnchors, type CommentHighlight } from "./comment-selection";
import { MarkdownPre, MarkdownSource, MarkdownTable } from "./markdown-block";
import styles from "./markdown.module.css";
import { syntaxHighlight } from "./syntax-code";

// Stable component types preserve controls and diagrams while later tokens arrive.
const components: Components = {
  a: ({ node: _node, children, ...props }) => (
    <a {...props} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),
  img: ({ alt }) => <span>[Image: {alt || "attachment"}]</span>,
  pre: MarkdownPre,
  table: MarkdownTable,
};

export const MessageText = memo(function MessageText({
  text,
  selectable = false,
  highlights,
}: {
  text: string;
  selectable?: boolean;
  highlights?: CommentHighlight[];
}) {
  const anchors = useMemo(
    () => (selectable ? [sourceAnchors(text, highlights)] : []),
    [text, selectable, highlights],
  );
  return (
    <MarkdownSource value={text}>
      <div className={styles.markdown}>
        <Markdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[syntaxHighlight, ...anchors]}
          skipHtml
          components={components}
        >
          {text}
        </Markdown>
      </div>
    </MarkdownSource>
  );
});

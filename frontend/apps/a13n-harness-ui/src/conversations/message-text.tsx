import { memo, useMemo } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { sourceAnchors } from "./comment-selection";
import styles from "./conversation.module.css";

export const MessageText = memo(function MessageText({
  text,
  selectable = false,
}: {
  text: string;
  selectable?: boolean;
}) {
  const anchors = useMemo(
    () => (selectable ? [sourceAnchors(text)] : []),
    [text, selectable],
  );
  return (
    <div className={styles.markdown}>
      <Markdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={anchors}
        skipHtml
        components={{
          a: ({ children, ...props }) => (
            <a {...props} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
          img: ({ alt }) => <span>[Image: {alt || "attachment"}]</span>,
        }}
      >
        {text}
      </Markdown>
    </div>
  );
});

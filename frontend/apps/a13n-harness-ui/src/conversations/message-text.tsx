import { memo, useContext, type ComponentPropsWithoutRef } from "react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { MarkdownPre, MarkdownSource, MarkdownTable } from "./markdown-block";
import styles from "./markdown.module.css";
import { linkedHostFile } from "../shell/page-links";
import { OpenHostFile } from "./tool-call";
import { syntaxHighlight } from "./syntax-code";

function MarkdownLink({
  href,
  children,
  ...props
}: ComponentPropsWithoutRef<"a">) {
  const openHostFile = useContext(OpenHostFile);
  const path = href ? linkedHostFile(href, window.location.href) : null;
  if (path && openHostFile)
    return (
      <a
        {...props}
        href={href}
        onClick={(event) => {
          event.preventDefault();
          openHostFile(path);
        }}
      >
        {children}
      </a>
    );
  if (path)
    return (
      <a {...props} href={href}>
        {children}
      </a>
    );
  return (
    <a {...props} href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  );
}

// Stable component types preserve controls and diagrams while later tokens arrive.
const components: Components = {
  a: ({ node: _node, ...props }) => <MarkdownLink {...props} />,
  img: ({ alt }) => <span>[Image: {alt || "attachment"}]</span>,
  pre: MarkdownPre,
  table: MarkdownTable,
};

export const MessageText = memo(function MessageText({
  text,
}: {
  text: string;
}) {
  return (
    <MarkdownSource value={text}>
      <div className={styles.markdown}>
        <Markdown
          remarkPlugins={[remarkGfm]}
          rehypePlugins={[syntaxHighlight]}
          skipHtml
          components={components}
        >
          {text}
        </Markdown>
      </div>
    </MarkdownSource>
  );
});

import { useTranslation } from "react-i18next";
import { memo } from "react";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import styles from "./markdown.module.css";
import { CodeBlock } from "./forms";

// Stable renderers preserve code-block state as a streamed answer grows.
const components: Components = {
  pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
  table: ({ children }) => <table className="a13n-scrollbar">{children}</table>,
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),
  img: function ImageLink({ src, alt }) {
    const { t } = useTranslation();
    // Remote content can contain tracking URLs; loading media is an explicit user action.
    return typeof src === "string" && /^https?:\/\//i.test(src) ? (
      <a href={src} target="_blank" rel="noopener noreferrer">
        {alt || t("Open image")}
      </a>
    ) : (
      <span>{alt || t("Image")}</span>
    );
  },
};

/** Unchanged messages do not parse Markdown again when their Thread updates. */
export const MarkdownContent = memo(function MarkdownContent({
  text,
  literalHtml = false,
}: {
  text: string;
  literalHtml?: boolean;
}) {
  return (
    <div className={styles.markdown}>
      <Markdown
        skipHtml={!literalHtml}
        remarkPlugins={[remarkGfm]}
        rehypePlugins={
          text.length <= 100_000 ? [[rehypeHighlight, { detect: false }]] : []
        }
        components={components}
      >
        {text}
      </Markdown>
    </div>
  );
});

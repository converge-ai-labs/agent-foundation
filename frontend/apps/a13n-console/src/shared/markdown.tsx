import { useTranslation } from "react-i18next";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import styles from "./markdown.module.css";
import { CodeBlock } from "./code-block";

export function MarkdownContent({
  text,
  literalHtml = false,
}: {
  text: string;
  literalHtml?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className={styles.markdown}>
      <Markdown
        skipHtml={!literalHtml}
        remarkPlugins={[remarkGfm]}
        rehypePlugins={
          text.length <= 100_000 ? [[rehypeHighlight, { detect: false }]] : []
        }
        components={{
          pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
          table: ({ children }) => (
            <table className="a13n-scrollbar">{children}</table>
          ),
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer">
              {children}
            </a>
          ),
          // Remote content can contain tracking URLs; loading media is an explicit user action.
          img: ({ src, alt }) =>
            typeof src === "string" && /^https?:\/\//i.test(src) ? (
              <a href={src} target="_blank" rel="noopener noreferrer">
                {alt || t("Open image")}
              </a>
            ) : (
              <span>{alt || t("Image")}</span>
            ),
        }}
      >
        {text}
      </Markdown>
    </div>
  );
}

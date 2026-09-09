import { useTranslation } from "react-i18next";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import styles from "./conversations.module.css";

export function MessageMarkdown({ text }: { text: string }) {
  const { t } = useTranslation();
  return (
    <div className={styles.markdown}>
      <Markdown
        skipHtml
        remarkPlugins={[remarkGfm]}
        components={{
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

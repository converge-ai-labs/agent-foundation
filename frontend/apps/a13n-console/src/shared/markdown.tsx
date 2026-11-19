import { useTranslation } from "react-i18next";
import { memo } from "react";
import {
  CaretRightIcon,
  PlayCircleIcon,
  RobotIcon,
} from "@phosphor-icons/react";
import { Link } from "react-router";
import { markdownResourceLink } from "./markdown-links";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import styles from "./markdown.module.css";
import { CodeBlock } from "./forms";

// Stable renderers preserve code-block state as a streamed answer grows.
const components: Components = {
  pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
  table: ({ children }) => <table className="a13n-scrollbar">{children}</table>,
  a: function MarkdownLink({ href, children, title }) {
    const { t } = useTranslation();
    const resource =
      typeof window === "undefined"
        ? null
        : markdownResourceLink(href, window.location.href);
    if (!resource)
      return (
        <a href={href} title={title} target="_blank" rel="noopener noreferrer">
          {children}
        </a>
      );
    const Icon = resource.kind === "agent" ? RobotIcon : PlayCircleIcon;
    // The analyst's plain-text type prefix is represented by the icon instead.
    const label =
      typeof children === "string"
        ? children.replace(
            resource.kind === "agent" ? /^Agent:\s+/i : /^Run:\s+/i,
            "",
          ) || children
        : children;
    return (
      <Link to={resource.to} title={title} className={styles.resourceLink}>
        <Icon size={14} aria-hidden="true" />
        <span className={styles.resourceLabel}>
          <span className="sr-only">
            {resource.kind === "agent" ? t("Agent") : t("Run")}:
          </span>{" "}
          {label}
        </span>
        <CaretRightIcon size={12} aria-hidden="true" />
      </Link>
    );
  },
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
  highlight = true,
}: {
  text: string;
  literalHtml?: boolean;
  highlight?: boolean;
}) {
  return (
    <div className={styles.markdown}>
      <Markdown
        skipHtml={!literalHtml}
        remarkPlugins={[remarkGfm]}
        rehypePlugins={
          highlight && text.length <= 100_000
            ? [[rehypeHighlight, { detect: false }]]
            : []
        }
        components={components}
      >
        {text}
      </Markdown>
    </div>
  );
});

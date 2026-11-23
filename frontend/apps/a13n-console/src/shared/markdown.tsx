import { useTranslation } from "react-i18next";
import { memo, type ComponentPropsWithoutRef } from "react";
import {
  ArrowUpRightIcon,
  CaretRightIcon,
  GlobeIcon,
  PlayCircleIcon,
  RobotIcon,
} from "@phosphor-icons/react";
import { Link } from "react-router";
import { markdownResourceLink } from "./markdown-links";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import rehypeKatex from "rehype-katex";
import remarkCjkFriendly from "remark-cjk-friendly";
import remarkMath from "remark-math";
import { remarkClosedDiagram, remarkLatex } from "a13n-ui/markdown-extensions";
import "katex/dist/katex.min.css";
import styles from "./markdown.module.css";
import { CodeBlock } from "./forms";
import { MarkdownDiagram } from "./markdown-diagram";

function MarkdownLink({
  href,
  children,
  title,
  compact = false,
}: ComponentPropsWithoutRef<"a"> & { compact?: boolean }) {
  const { t } = useTranslation();
  const resource =
    typeof window === "undefined"
      ? null
      : markdownResourceLink(href, window.location.href);
  if (!resource && (!compact || !href))
    return (
      <a href={href} title={title} target="_blank" rel="noopener noreferrer">
        {children}
      </a>
    );
  const Icon = !resource
    ? GlobeIcon
    : resource.kind === "agent"
      ? RobotIcon
      : PlayCircleIcon;
  // The analyst's plain-text type prefix is represented by the icon instead.
  const label =
    resource && typeof children === "string"
      ? children.replace(
          resource.kind === "agent" ? /^Agent:\s+/i : /^Run:\s+/i,
          "",
        ) || children
      : children;
  const content = (
    <>
      <Icon size={14} aria-hidden="true" />
      <span className={styles.resourceLabel}>
        {resource && (
          <>
            <span className="sr-only">
              {resource.kind === "agent" ? t("Agent") : t("Run")}:
            </span>{" "}
          </>
        )}
        {label}
      </span>
      {resource ? (
        <CaretRightIcon size={12} aria-hidden="true" />
      ) : (
        <ArrowUpRightIcon size={12} aria-hidden="true" />
      )}
    </>
  );
  return resource ? (
    <Link to={resource.to} title={title} className={styles.resourceLink}>
      {content}
    </Link>
  ) : (
    <a
      href={href}
      title={title}
      target="_blank"
      rel="noopener noreferrer"
      className={styles.resourceLink}
    >
      {content}
    </a>
  );
}

function ImageLink({
  src,
  alt,
  compact = false,
}: ComponentPropsWithoutRef<"img"> & { compact?: boolean }) {
  const { t } = useTranslation();
  // Remote content can contain tracking URLs; loading media is an explicit user action.
  return typeof src === "string" && /^https?:\/\//i.test(src) ? (
    <MarkdownLink href={src} compact={compact}>
      {alt || t("Open image")}
    </MarkdownLink>
  ) : (
    <span>{alt || t("Image")}</span>
  );
}

// Stable renderers preserve code-block state as a streamed answer grows.
const components: Components = {
  pre: ({ children }) => <CodeBlock>{children}</CodeBlock>,
  table: ({ children }) => <table className="a13n-scrollbar">{children}</table>,
  a: MarkdownLink,
  img: ImageLink,
};
const compactComponents: Components = {
  ...components,
  a: (props) => <MarkdownLink {...props} compact />,
  img: (props) => <ImageLink {...props} compact />,
};

function MathRegion(props: ComponentPropsWithoutRef<"span">) {
  const { t } = useTranslation();
  return (
    <span
      {...props}
      role="region"
      aria-label={t("Math formula")}
      tabIndex={0}
    />
  );
}

const replyComponents: Components = {
  ...components,
  pre: ({ node, children }) => {
    const code = node?.children.find(
      (child) => child.type === "element" && child.tagName === "code",
    );
    const source = code?.type === "element" ? code.children[0] : undefined;
    return code?.type === "element" &&
      code.properties.dataClosedDiagram === true &&
      source?.type === "text" ? (
      <MarkdownDiagram source={source.value}>{children}</MarkdownDiagram>
    ) : (
      <CodeBlock>{children}</CodeBlock>
    );
  },
  span: ({ node: _node, ...props }) =>
    props.className === "katex-display" ? (
      <MathRegion {...props} />
    ) : (
      <span {...props} />
    ),
};

/** Unchanged messages do not parse Markdown again when their Thread updates. */
export const MarkdownContent = memo(function MarkdownContent({
  text,
  literalHtml = false,
  highlight = true,
  compactLinks = false,
  agentReply = false,
}: {
  text: string;
  literalHtml?: boolean;
  highlight?: boolean;
  compactLinks?: boolean;
  agentReply?: boolean;
}) {
  return (
    <div className={styles.markdown}>
      <Markdown
        skipHtml={!literalHtml}
        remarkPlugins={
          agentReply
            ? [
                remarkGfm,
                remarkCjkFriendly,
                remarkMath,
                remarkLatex,
                remarkClosedDiagram,
              ]
            : [remarkGfm]
        }
        rehypePlugins={[
          ...(agentReply ? [rehypeKatex] : []),
          ...(highlight && text.length <= 100_000
            ? [
                [rehypeHighlight, { detect: false }] as [
                  typeof rehypeHighlight,
                  { detect: boolean },
                ],
              ]
            : []),
        ]}
        components={
          agentReply
            ? replyComponents
            : compactLinks
              ? compactComponents
              : components
        }
      >
        {text}
      </Markdown>
    </div>
  );
});

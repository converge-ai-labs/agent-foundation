import { useTranslation } from "../i18n";
import {
  memo,
  useContext,
  useMemo,
  type ComponentPropsWithoutRef,
} from "react";
import { useGuestAccess } from "../transport/context";
import Markdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkCjkFriendly from "remark-cjk-friendly/parseOnly";
import remarkMath from "remark-math";
import {
  remarkCitations,
  remarkClosedDiagram,
  remarkLatex,
} from "a13n-ui/markdown-extensions";
import { prepareCitations, type Citation } from "a13n-ui/citations";
import { CitationMark, CitationMarkdown } from "a13n-ui/citation-markdown";
import rehypeKatex from "rehype-katex";
import "katex/dist/katex.min.css";
import { HtmlPreviewEnabled } from "a13n-ui/html-code-preview";
import { MarkdownPre, MarkdownTable } from "./markdown-block";
import styles from "./markdown.module.css";
import { linkedHostFile } from "../shell/page-links";
import { OpenHostFile } from "./tool-call";
import { linkedMediaPreviews } from "./linked-media";
import { LinkedMediaPreview } from "./linked-media-preview";

function MarkdownLink({
  href,
  children,
  ...props
}: ComponentPropsWithoutRef<"a">) {
  const openHostFile = useContext(OpenHostFile);
  const guest = useGuestAccess();
  if (guest && href) {
    try {
      if (new URL(href, window.location.href).origin === window.location.origin)
        return <span>{children}</span>;
    } catch {
      return <span>{children}</span>;
    }
  }
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

function MarkdownImage({ alt }: ComponentPropsWithoutRef<"img">) {
  const { t } = useTranslation();
  return (
    <span>
      {t("[Image: {{description}}]", { description: alt || t("attachment") })}
    </span>
  );
}

// A stable component keeps the formula region translated without remounting KaTeX output.
function MarkdownSpan({
  className,
  ...props
}: ComponentPropsWithoutRef<"span">) {
  const { t } = useTranslation();
  return (
    <span
      {...props}
      className={className}
      {...(className === "katex-display"
        ? { role: "region", "aria-label": t("Math formula"), tabIndex: 0 }
        : {})}
    />
  );
}

// Stable component types preserve controls and diagrams while later tokens arrive.
const components: Components & { "a13n-citation": typeof CitationMark } = {
  "a13n-citation": CitationMark,
  a: ({ node: _node, ...props }) => <MarkdownLink {...props} />,
  img: ({ node: _node, ...props }) => <MarkdownImage {...props} />,
  pre: MarkdownPre,
  table: MarkdownTable,
  span: ({ node: _node, ...props }) => <MarkdownSpan {...props} />,
  figure: ({ node, children, ...props }) => {
    const path = node?.properties.dataHostMediaPath;
    return typeof path === "string" ? (
      <LinkedMediaPreview path={path} />
    ) : (
      <figure {...props}>{children}</figure>
    );
  },
};

export const MessageText = memo(function MessageText({
  text,
  previewHtml = false,
  citations,
}: {
  text: string;
  previewHtml?: boolean;
  citations?: readonly Citation[];
}) {
  const guest = useGuestAccess();
  const { t } = useTranslation();
  const prepared = useMemo(
    () => prepareCitations({ text, citations }),
    [text, citations],
  );
  return (
    <div className={styles.markdown}>
      <CitationMarkdown
        sources={prepared.sources}
        allowLink={(url) => !guest || url.origin !== window.location.origin}
        labels={{
          unavailable: t("Source unavailable"),
          document: t("Cited document"),
          source: (number, name) =>
            t("Source {{number}}: {{name}}", { number, name }),
          sources: (count) => t("Sources ({{count}})", { count }),
        }}
      >
        <HtmlPreviewEnabled value={previewHtml}>
          <Markdown
            remarkPlugins={[
              remarkGfm,
              remarkCjkFriendly,
              remarkMath,
              remarkLatex,
              remarkClosedDiagram,
              [remarkCitations, prepared],
            ]}
            rehypePlugins={
              guest
                ? [rehypeKatex]
                : [
                    rehypeKatex,
                    [
                      linkedMediaPreviews,
                      { currentHref: window.location.href },
                    ],
                  ]
            }
            skipHtml
            components={components}
          >
            {prepared.markdown}
          </Markdown>
        </HtmlPreviewEnabled>
      </CitationMarkdown>
    </div>
  );
});

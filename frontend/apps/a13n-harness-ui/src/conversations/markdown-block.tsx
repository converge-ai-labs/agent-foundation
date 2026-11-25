import { useTranslation } from "../i18n";
import {
  useEffect,
  useState,
  type ComponentProps,
  type ReactNode,
} from "react";
import type { ExtraProps } from "react-markdown";
import { Button } from "a13n-ui";
import { HtmlCodePreview } from "a13n-ui/html-code-preview";
import { markdownCodeText } from "a13n-ui/markdown-extensions";
import { ArrowsOut, Copy, Code } from "@phosphor-icons/react";
import { ImagePreview } from "../shell/image-preview";
import { renderDiagram } from "a13n-ui/mermaid-render";
import { SyntaxCode } from "./syntax-code";
import styles from "./markdown.module.css";
import { copyText } from "../platform";

function Diagram({
  source,
  header,
  children,
}: {
  source: string;
  header: (actions: ReactNode) => ReactNode;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const [dark, setDark] = useState(() =>
    document.documentElement.classList.contains("dark"),
  );
  const [result, setResult] = useState<{
    source: string;
    preview: Awaited<ReturnType<typeof renderDiagram>>;
  }>();
  const [original, setOriginal] = useState(false);
  const [expanded, setExpanded] = useState(false);
  useEffect(() => {
    const observer = new MutationObserver(() =>
      setDark(document.documentElement.classList.contains("dark")),
    );
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    let active = true;
    void renderDiagram(source, dark).then((preview) => {
      if (active) setResult({ source, preview });
    });
    return () => {
      active = false;
    };
  }, [source, dark]);
  // Retain the previous theme's image during recoloring, but never a different diagram.
  const current = result?.source === source ? result.preview : undefined;
  return (
    <>
      {header(
        <>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setOriginal(!original)}
            aria-pressed={original}
            aria-label={t("Source")}
          >
            <Code />
            <span className={styles.actionLabel}>{t("Source")}</span>
          </Button>
          <Button
            variant="ghost"
            size="sm"
            disabled={!current?.src}
            onClick={() => setExpanded(true)}
            aria-label={t("Expand")}
          >
            <ArrowsOut />
            <span className={styles.actionLabel}>{t("Expand")}</span>
          </Button>
        </>,
      )}
      {!original && current?.src && (
        <div className={styles.diagramCanvas} data-diagram-canvas>
          <img src={current.src} alt={t("Mermaid diagram")} />
        </div>
      )}
      {!current && (
        <p className={styles.diagramStatus} role="status">
          {t("Rendering diagram…")}
        </p>
      )}
      {current?.reason && (
        <p className={styles.diagramStatus} role="status">
          {t(current.reason)}
        </p>
      )}
      {(original || !current?.src) && (
        <pre tabIndex={0} role="region" aria-label={t("Diagram source")}>
          {children}
        </pre>
      )}
      {expanded && current?.src && (
        <ImagePreview
          src={current.src}
          name={t("Mermaid diagram")}
          close={() => setExpanded(false)}
        />
      )}
    </>
  );
}

export function MarkdownPre({
  node,
  children,
}: ComponentProps<"pre"> & ExtraProps) {
  const { t } = useTranslation();
  const code = node?.children.find(
    (item) => item.type === "element" && item.tagName === "code",
  );
  const classes =
    code?.type === "element" ? code.properties.className : undefined;
  const language = Array.isArray(classes)
    ? String(
        classes.find((item) => String(item).startsWith("language-")) ?? "",
      ).replace("language-", "")
    : "";
  const source = markdownCodeText(code);
  const diagram = language.toLowerCase() === "mermaid";
  const complete =
    code?.type === "element" && code.properties.dataClosedDiagram === true;
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(timer);
  }, [copied]);
  // One header row owns every block action; diagrams add Source and Expand
  // before Copy instead of stacking a second toolbar above the canvas.
  const header = (actions?: ReactNode) => (
    <>
      <div className={styles.codeHeader}>
        <span className={styles.codeLanguage}>
          {diagram ? "mermaid" : language || "text"}
        </span>
        <div className={styles.codeActions}>
          {actions}
          <Button
            variant="ghost"
            size="sm"
            aria-label={
              diagram ? t("Copy diagram source") : t("Copy code block")
            }
            onClick={async () => {
              try {
                await copyText(source);
                setCopied(true);
                setCopyError(false);
              } catch {
                setCopyError(true);
              }
            }}
          >
            <Copy />
            {copied ? t("Copied") : t("Copy")}
          </Button>
        </div>
      </div>
      {copyError && (
        <p className={styles.diagramStatus} role="status">
          {t("Could not copy. Select the source to copy it manually.")}
        </p>
      )}
    </>
  );
  const codeSource = (
    <pre
      tabIndex={0}
      role="region"
      aria-label={
        language ? t("{{language}} code", { language }) : t("Code block")
      }
    >
      <SyntaxCode source={source} language={language} />
    </pre>
  );
  return (
    <div className={styles.codeBlock}>
      {language.toLowerCase() === "html" ? (
        <HtmlCodePreview
          source={source}
          labels={{
            preview: t("Preview"),
            title: t("HTML preview"),
            tooLarge: t("HTML is too large to preview (200 KB limit)."),
          }}
        >
          {({ actions, preview, hideSource }) => (
            <>
              {header(actions)}
              {preview}
              {!hideSource && codeSource}
            </>
          )}
        </HtmlCodePreview>
      ) : diagram && complete ? (
        <Diagram source={source} header={header}>
          {children}
        </Diagram>
      ) : (
        <>
          {header()}
          {diagram && (
            <p className={styles.diagramStatus}>
              {t("Waiting for the complete diagram…")}
            </p>
          )}
          {codeSource}
        </>
      )}
    </div>
  );
}

export function MarkdownTable({
  node: _node,
  children,
  ...props
}: ComponentProps<"table"> & ExtraProps) {
  const { t } = useTranslation();
  return (
    <div
      className={styles.tableScroll}
      role="region"
      aria-label={t("Table")}
      tabIndex={0}
    >
      <table {...props}>{children}</table>
    </div>
  );
}

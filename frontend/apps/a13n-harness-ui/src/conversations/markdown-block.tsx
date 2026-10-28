import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ComponentProps,
  type ReactNode,
} from "react";
import type { ExtraProps } from "react-markdown";
import { Button } from "a13n-ui";
import { ArrowsOut, Copy, Code } from "@phosphor-icons/react";
import { ImagePreview } from "../shell/image-preview";
import { renderDiagram } from "./mermaid-render";
import { SyntaxCode } from "./syntax-code";
import styles from "./markdown.module.css";
import { copyText } from "../platform";

export const MarkdownSource = createContext("");
type Node = { type: string; value?: string; children?: Node[] };
function nodeText(node?: Node): string {
  return node?.type === "text"
    ? (node.value ?? "")
    : (node?.children?.map(nodeText).join("") ?? "");
}
export function closedFence(source: string) {
  const lines = source.trimEnd().split("\n");
  const fence = /^ {0,3}(`{3,}|~{3,})/.exec(lines[0]);
  if (!fence || lines.length < 2) return false;
  const closing = /^ {0,3}(`{3,}|~{3,})\s*$/.exec(lines.at(-1)!);
  return (
    !!closing &&
    closing[1][0] === fence[1][0] &&
    closing[1].length >= fence[1].length
  );
}

function Diagram({
  source,
  header,
  children,
}: {
  source: string;
  header: (actions: ReactNode) => ReactNode;
  children: ReactNode;
}) {
  const [dark, setDark] = useState(() =>
    document.documentElement.classList.contains("dark"),
  );
  const [result, setResult] = useState<{
    source: string;
    src?: string;
    error?: string;
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
    void renderDiagram(source, dark).then(
      (src) => {
        if (active) setResult({ source, src });
      },
      () => {
        if (active)
          setResult({
            source,
            error:
              "Diagram preview unavailable. The original source is shown below.",
          });
      },
    );
    return () => {
      active = false;
    };
  }, [source, dark]);
  // Retain the previous theme's image during recoloring, but never a different diagram.
  const current = result?.source === source ? result : undefined;
  return (
    <>
      {header(
        <>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setOriginal(!original)}
            aria-pressed={original}
            aria-label="Source"
          >
            <Code />
            <span className={styles.actionLabel}>Source</span>
          </Button>
          <Button
            variant="ghost"
            size="sm"
            disabled={!current?.src}
            onClick={() => setExpanded(true)}
            aria-label="Expand"
          >
            <ArrowsOut />
            <span className={styles.actionLabel}>Expand</span>
          </Button>
        </>,
      )}
      {!original && current?.src && (
        <div className={styles.diagramCanvas} data-diagram-canvas>
          <img src={current.src} alt="Mermaid diagram" />
        </div>
      )}
      {!current && (
        <p className={styles.diagramStatus} role="status">
          Rendering diagram…
        </p>
      )}
      {current?.error && (
        <p className={styles.diagramStatus} role="status">
          {current.error}
        </p>
      )}
      {(original || !current?.src) && (
        <pre tabIndex={0} role="region" aria-label="Diagram source">
          {children}
        </pre>
      )}
      {expanded && current?.src && (
        <ImagePreview
          src={current.src}
          name="Mermaid diagram"
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
  const markdown = useContext(MarkdownSource);
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
  const source = nodeText(code);
  const start = node?.position?.start.offset;
  const end = node?.position?.end.offset;
  const diagram = language.toLowerCase() === "mermaid";
  const complete =
    start !== undefined &&
    end !== undefined &&
    closedFence(markdown.slice(start, end));
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
            aria-label={diagram ? "Copy diagram source" : "Copy code"}
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
            {copied ? "Copied" : "Copy"}
          </Button>
        </div>
      </div>
      {copyError && (
        <p className={styles.diagramStatus} role="status">
          Could not copy. Select the source to copy it manually.
        </p>
      )}
    </>
  );
  return (
    <div className={styles.codeBlock}>
      {diagram && complete ? (
        <Diagram source={source} header={header}>
          {children}
        </Diagram>
      ) : (
        <>
          {header()}
          {diagram && (
            <p className={styles.diagramStatus}>
              Waiting for the complete diagram…
            </p>
          )}
          <pre
            tabIndex={0}
            role="region"
            aria-label={language ? `${language} code` : "Code block"}
          >
            <SyntaxCode source={source} language={language} />
          </pre>
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
  return (
    <div
      className={styles.tableScroll}
      role="region"
      aria-label="Table"
      tabIndex={0}
    >
      <table {...props}>{children}</table>
    </div>
  );
}

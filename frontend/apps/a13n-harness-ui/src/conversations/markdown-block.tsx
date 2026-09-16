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
import { ImagePreview } from "./image-preview";
import { renderDiagram } from "./mermaid-render";
import styles from "./markdown.module.css";

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
  children,
}: {
  source: string;
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
      <div className={styles.diagramActions}>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setOriginal(!original)}
          aria-pressed={original}
        >
          <Code />
          Source
        </Button>
        <Button
          variant="ghost"
          size="sm"
          disabled={!current?.src}
          onClick={() => setExpanded(true)}
        >
          <ArrowsOut />
          Expand
        </Button>
      </div>
      {!original && current?.src && (
        <div className={styles.diagramCanvas}>
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
      {(original || !current?.src) && <pre>{children}</pre>}
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
  return (
    <div className={styles.codeBlock}>
      <div className={styles.codeHeader}>
        <span>{diagram ? "Mermaid" : language || "Code"}</span>
        <Button
          variant="ghost"
          size="sm"
          aria-label={diagram ? "Copy diagram source" : "Copy code"}
          onClick={async () => {
            try {
              await navigator.clipboard.writeText(source);
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
      {copyError && (
        <p className={styles.diagramStatus} role="status">
          Could not copy. Select the source to copy it manually.
        </p>
      )}
      {diagram && complete ? (
        <Diagram source={source}>{children}</Diagram>
      ) : (
        <>
          {diagram && (
            <p className={styles.diagramStatus}>
              Waiting for the complete diagram…
            </p>
          )}
          <pre>{children}</pre>
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

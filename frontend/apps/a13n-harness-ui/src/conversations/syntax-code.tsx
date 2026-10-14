import { memo, useMemo, type ReactNode } from "react";
import { common, createLowlight } from "lowlight";
import styles from "./syntax-code.module.css";

const highlighter = createLowlight(common);
type CodeNode = {
  type: string;
  value?: string;
  tagName?: string;
  properties?: Record<string, unknown>;
  children?: CodeNode[];
};
export function codeLanguage(path = "") {
  const extension = path.split(".").at(-1)?.toLowerCase() ?? "";
  return (
    (
      {
        py: "python",
        js: "javascript",
        jsx: "javascript",
        ts: "typescript",
        tsx: "typescript",
        yml: "yaml",
        sh: "bash",
        rs: "rust",
        md: "markdown",
        h: "cpp",
      } as Record<string, string>
    )[extension] ?? extension
  );
}
function highlighted(source: string, language: string): CodeNode[] | undefined {
  if (
    source.length > 256 * 1024 ||
    !language ||
    !highlighter.registered(language)
  )
    return;
  try {
    return highlighter.highlight(language, source).children as CodeNode[];
  } catch {
    return;
  }
}
function tokens(nodes: CodeNode[]): ReactNode {
  return nodes.map((node, index) =>
    node.type === "text" ? (
      node.value
    ) : (
      <span
        key={index}
        className={
          Array.isArray(node.properties?.className)
            ? node.properties.className.join(" ")
            : undefined
        }
      >
        {tokens(node.children ?? [])}
      </span>
    ),
  );
}
export const SyntaxCode = memo(function SyntaxCode({
  source,
  language = "",
}: {
  source: string;
  language?: string;
}) {
  const nodes = useMemo(
    () => highlighted(source, language),
    [source, language],
  );
  return (
    <code className={styles.syntax}>{nodes ? tokens(nodes) : source}</code>
  );
});

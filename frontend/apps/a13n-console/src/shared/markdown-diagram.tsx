import { CodeIcon } from "@phosphor-icons/react";
import { Button } from "a13n-ui";
import { renderDiagram } from "a13n-ui/mermaid-render";
import {
  useEffect,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import { CodeBlock } from "./forms/code-block";
import styles from "./markdown.module.css";

function subscribeTheme(onChange: () => void) {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["class"],
  });
  return () => observer.disconnect();
}
const isDark = () => document.documentElement.classList.contains("dark");

export function MarkdownDiagram({
  source,
  children,
}: {
  source: string;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const dark = useSyncExternalStore(subscribeTheme, isDark, () => false);
  const [showSource, setShowSource] = useState(false);
  const [result, setResult] = useState<{
    source: string;
    src: string | null;
  }>();
  useEffect(() => {
    let live = true;
    void renderDiagram(source, dark).then(
      (src) => live && setResult({ source, src }),
      () => live && setResult({ source, src: null }),
    );
    return () => {
      live = false;
    };
  }, [source, dark]);

  // Preserve the old theme's image while recoloring, never an earlier source.
  const current = result?.source === source ? result : undefined;
  const src = current?.src;
  const label = t("Mermaid diagram");
  const sourceLabel = t("Diagram source");
  return (
    <CodeBlock
      copyLabel={t("Copy diagram source")}
      sourceLabel={sourceLabel}
      hideSource={!!src && !showSource}
      actions={
        src && (
          <Button
            variant="ghost"
            size="sm"
            aria-pressed={showSource}
            onClick={() => setShowSource((shown) => !shown)}
          >
            <CodeIcon aria-hidden="true" />
            {sourceLabel}
          </Button>
        )
      }
      preview={
        src ? (
          !showSource && (
            <div
              className={`${styles.diagramCanvas} a13n-scrollbar`}
              role="region"
              aria-label={label}
              tabIndex={0}
            >
              <img src={src} alt={label} />
            </div>
          )
        ) : (
          <p className={styles.diagramStatus} role="status">
            {current
              ? t(
                  "Diagram preview unavailable. The original source is shown below.",
                )
              : t("Rendering diagram…")}
          </p>
        )
      }
    >
      {children}
    </CodeBlock>
  );
}

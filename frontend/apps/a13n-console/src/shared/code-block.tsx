import { Children, isValidElement, useRef, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { CopyButton } from "./copy";
import styles from "./markdown.module.css";

export function CodeBlock({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const content = useRef<HTMLPreElement>(null);
  const code = Children.toArray(children).find(
    (child) => isValidElement(child) && child.type === "code",
  );
  const language = isValidElement<{ className?: string }>(code)
    ? code.props.className?.match(/language-([\w-]+)/)?.[1]
    : undefined;
  return (
    <div className={styles.codeBlock}>
      <div className={styles.codeToolbar}>
        <span>{language ?? t("Text")}</span>
        <CopyButton
          value={() => content.current?.textContent ?? ""}
          copyLabel={t("Copy code")}
        />
      </div>
      <pre ref={content} className="a13n-scrollbar">
        {children}
      </pre>
    </div>
  );
}

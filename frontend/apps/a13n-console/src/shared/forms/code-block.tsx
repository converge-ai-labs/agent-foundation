import { Children, isValidElement, useRef, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { CopyButton } from "../identity";
import styles from "../markdown.module.css";

export function CodeBlock({
  children,
  actions,
  preview,
  hideSource = false,
  copyLabel,
  sourceLabel,
}: {
  children: ReactNode;
  actions?: ReactNode;
  preview?: ReactNode;
  hideSource?: boolean;
  copyLabel?: string;
  sourceLabel?: string;
}) {
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
        {/* The toolbar names the language; copying is a quiet control beside it. */}
        <div className={styles.codeActions}>
          {actions}
          <CopyButton
            value={() => content.current?.textContent ?? ""}
            iconOnly
            copyLabel={copyLabel ?? t("Copy code")}
          />
        </div>
      </div>
      {preview}
      <pre
        ref={content}
        hidden={hideSource}
        className="a13n-scrollbar"
        {...(sourceLabel
          ? { role: "region", "aria-label": sourceLabel, tabIndex: 0 }
          : {})}
      >
        {children}
      </pre>
    </div>
  );
}

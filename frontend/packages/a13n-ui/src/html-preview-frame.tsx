import { useMemo } from "react";
import { buildHtmlPreviewDocument } from "./html-preview-document";
import styles from "./html-code-preview.module.css";

export default function HtmlPreviewFrame({
  source,
  title,
}: {
  source: string;
  title: string;
}) {
  const srcDoc = useMemo(() => buildHtmlPreviewDocument(source), [source]);
  return (
    <iframe
      className={styles.frame}
      title={title}
      sandbox=""
      referrerPolicy="no-referrer"
      srcDoc={srcDoc}
    />
  );
}

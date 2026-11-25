import { EyeIcon } from "@phosphor-icons/react";
import {
  Suspense,
  createContext,
  lazy,
  useContext,
  useState,
  type ReactNode,
} from "react";
import { Button } from "./components/button";
import styles from "./html-code-preview.module.css";

const HtmlPreviewFrame = lazy(() => import("./html-preview-frame"));

export const HtmlPreviewEnabled = createContext(false);

export function isHtmlPreviewTooLarge(source: string) {
  const limit = 200_000;
  return (
    source.length > limit || new TextEncoder().encode(source).length > limit
  );
}

export function HtmlCodePreview({
  source,
  labels,
  children,
}: {
  source: string;
  labels: { preview: string; title: string; tooLarge: string };
  children: (view: {
    actions?: ReactNode;
    preview?: ReactNode;
    hideSource?: boolean;
  }) => ReactNode;
}) {
  const enabled = useContext(HtmlPreviewEnabled);
  const [selection, setSelection] = useState<{
    source: string;
    tooLarge: boolean;
  }>();
  const active = enabled && selection?.source === source;
  const hideSource = active && !selection.tooLarge;
  return children({
    actions: enabled && (
      <Button
        variant="ghost"
        size="sm"
        className={styles.toggle}
        aria-pressed={active}
        onClick={() =>
          setSelection(
            active
              ? undefined
              : { source, tooLarge: isHtmlPreviewTooLarge(source) },
          )
        }
      >
        <EyeIcon aria-hidden="true" />
        {labels.preview}
      </Button>
    ),
    hideSource,
    preview:
      active &&
      (hideSource ? (
        <Suspense fallback={<div className={styles.frame} />}>
          <HtmlPreviewFrame source={source} title={labels.title} />
        </Suspense>
      ) : (
        <p role="status" className={styles.status}>
          {labels.tooLarge}
        </p>
      )),
  });
}

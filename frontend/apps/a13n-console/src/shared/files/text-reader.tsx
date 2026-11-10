import {
  ArrowDownIcon,
  ArrowUpIcon,
  ColumnsIcon,
  RowsIcon,
  TextAlignLeftIcon,
  CopyIcon,
  ArrowsOutSimpleIcon,
} from "@phosphor-icons/react";
import { Button } from "a13n-ui";
import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { useTranslation } from "react-i18next";
import { ErrorNotice, Loading } from "../feedback";
import { textFormat } from "./text-model";
import styles from "./reader.module.css";

const TextEditor = lazy(() => import("./text-editor"));

/** Full raw content, never the bounded server patch. */
export function TextReader({
  path = "",
  text,
  before,
  beforeLabel,
  afterLabel,
}: {
  path?: string;
  text: string;
  before?: string;
  beforeLabel?: string;
  afterLabel?: string;
}) {
  const { t } = useTranslation();
  const host = useRef<HTMLDivElement>(null);
  const [wide, setWide] = useState(false);
  const [layout, setLayout] = useState<"auto" | "inline" | "split">("auto");
  const [wrap, setWrap] = useState(true);
  const [collapse, setCollapse] = useState(true);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState<unknown>();
  const navigate = useRef<(forward: boolean) => void>(() => {});
  const onNavigate = useCallback((value: (forward: boolean) => void) => {
    navigate.current = value;
  }, []);
  useEffect(() => {
    if (!host.current) return;
    const observer = new ResizeObserver(([entry]) =>
      setWide(entry.contentRect.width >= 960),
    );
    observer.observe(host.current);
    return () => observer.disconnect();
  }, []);
  const diff = before !== undefined;
  const split = wide && layout !== "inline";
  const format = (value: string) => {
    const info = textFormat(value);
    return `${info.endings === "Mixed" ? t("Mixed line endings") : info.endings}${value && !info.finalNewline ? ` · ${t("No newline at end of file")}` : ""}`;
  };
  return (
    <div className={styles.reader} ref={host}>
      <div className={styles.tools}>
        <span className={styles.format}>
          {diff
            ? `${t("Before")}: ${format(before)} → ${t("After")}: ${format(text)}`
            : format(text)}
        </span>
        {diff && (
          <>
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label={t("Previous change")}
              title={t("Previous change")}
              onClick={() => navigate.current(false)}
            >
              <ArrowUpIcon />
            </Button>
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label={t("Next change")}
              title={t("Next change")}
              onClick={() => navigate.current(true)}
            >
              <ArrowDownIcon />
            </Button>
            <Button
              variant="ghost"
              size="icon-xs"
              disabled={!wide}
              aria-label={t(split ? "Inline diff" : "Split diff")}
              title={t(split ? "Inline diff" : "Split diff")}
              onClick={() => setLayout(split ? "inline" : "split")}
            >
              <>{split ? <RowsIcon /> : <ColumnsIcon />}</>
            </Button>
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label={t("Show all context")}
              title={t("Show all context")}
              aria-pressed={!collapse}
              onClick={() => setCollapse(!collapse)}
            >
              <ArrowsOutSimpleIcon />
            </Button>
          </>
        )}
        <Button
          variant="ghost"
          size="icon-xs"
          aria-label={t("Wrap lines")}
          title={t("Wrap lines")}
          aria-pressed={wrap}
          onClick={() => setWrap(!wrap)}
        >
          <TextAlignLeftIcon />
        </Button>
        {!diff && (
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label={t(copied ? "Copied" : "Copy file")}
            title={t(copied ? "Copied" : "Copy file")}
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(text);
                setCopied(true);
                setCopyError(undefined);
              } catch (error) {
                setCopyError(error);
              }
            }}
          >
            <CopyIcon />
          </Button>
        )}
      </div>
      <ErrorNotice error={copyError} />
      {diff && (
        <div className={styles.labels}>
          <span>
            {split
              ? (beforeLabel ?? t("Before: saved baseline"))
              : t("Inline diff: removed (−), added (+)")}
          </span>
          {split && <span>{afterLabel ?? t("After: candidate")}</span>}
        </div>
      )}
      <Suspense fallback={<Loading variant="code" />}>
        <TextEditor
          path={path}
          text={text}
          before={before}
          split={split}
          wrap={wrap}
          collapse={collapse}
          label={afterLabel ?? path}
          beforeLabel={beforeLabel}
          onNavigate={onNavigate}
        />
      </Suspense>
    </div>
  );
}

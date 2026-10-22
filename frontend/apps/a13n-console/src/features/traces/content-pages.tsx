import { Button } from "a13n-ui";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { CopyButton } from "../../shared/identity";
import styles from "./traces.module.css";

const ITEMS_PER_PAGE = 10;
export const TEXT_PAGE_SIZE = 20_000;

/** Bind navigation to its content, including when a cached span is revisited. */
function useContentPage(source: unknown, length: number, size: number) {
  const [selection, setSelection] = useState({ source, page: 0 });
  const current = selection.source === source;
  if (!current) setSelection({ source, page: 0 });
  const page = current ? selection.page : 0;
  const start = page * size;
  return {
    start,
    end: Math.min(start + size, length),
    previous:
      page > 0 ? () => setSelection({ source, page: page - 1 }) : undefined,
    next:
      start + size < length
        ? () => setSelection({ source, page: page + 1 })
        : undefined,
  };
}

function ContentPagination({
  page,
  children,
}: {
  page: ReturnType<typeof useContentPage>;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <nav className={styles.contentPagination} aria-label={t("Content pages")}>
      <span aria-live="polite">{children}</span>
      <div>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={!page.previous}
          onClick={page.previous}
        >
          {t("Previous")}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={!page.next}
          onClick={page.next}
        >
          {t("Next")}
        </Button>
      </div>
    </nav>
  );
}

/** Only the current page mounts renderers, Markdown parsers and code blocks. */
export function ContentSequence({
  items,
  render,
}: {
  items: readonly unknown[];
  render: (item: unknown, index: number) => ReactNode;
}) {
  const { t } = useTranslation();
  const page = useContentPage(items, items.length, ITEMS_PER_PAGE);
  return (
    <div className={styles.contentSequence}>
      {items.length > ITEMS_PER_PAGE && (
        <ContentPagination page={page}>
          {t("Items {{start}}–{{end}} of {{total}}", {
            start: page.start + 1,
            end: page.end,
            total: items.length,
          })}
        </ContentPagination>
      )}
      {items
        .slice(page.start, page.end)
        .map((item, index) => render(item, page.start + index))}
    </div>
  );
}

/** Large source stays complete without parsing or laying out it all at once. */
export function PagedSource({ text }: { text: string }) {
  const { t } = useTranslation();
  const page = useContentPage(text, text.length, TEXT_PAGE_SIZE);
  // Avoid splitting a UTF-16 surrogate pair across adjacent pages.
  const boundary = (index: number) =>
    index > 0 &&
    index < text.length &&
    /[\uD800-\uDBFF]/.test(text[index - 1]!) &&
    /[\uDC00-\uDFFF]/.test(text[index]!)
      ? index - 1
      : index;
  const start = boundary(page.start),
    end = boundary(page.end);
  return (
    <div className={styles.contentSequence}>
      <ContentPagination page={page}>
        {t("Plain text · {{start}}–{{end}} of {{total}} characters", {
          start: start + 1,
          end,
          total: text.length,
        })}
      </ContentPagination>
      <pre className={`${styles.rawContent} a13n-scrollbar`}>
        {text.slice(start, end)}
      </pre>
      <CopyButton value={text} copyLabel={t("Copy full text")} />
    </div>
  );
}

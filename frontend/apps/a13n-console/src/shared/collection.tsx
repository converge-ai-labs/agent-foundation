import { useState, type ReactNode } from "react";
import { Button } from "a13n-ui";
import { useTranslation } from "react-i18next";
import styles from "./shared.module.css";

export function Table<T extends { id: string }>({
  items,
  columns,
  caption,
}: {
  items: readonly T[];
  columns: readonly { label: string; render: (item: T) => ReactNode }[];
  caption?: string;
}) {
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        {caption && <caption className="visually-hidden">{caption}</caption>}
        <thead>
          <tr>
            {columns.map((column) => (
              <th scope="col" key={column.label}>
                {column.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.id}>
              {columns.map((column) => (
                <td key={column.label}>{column.render(item)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
export function useCursor() {
  const [history, setHistory] = useState<(string | undefined)[]>([undefined]);
  return {
    cursor: history[history.length - 1],
    next: (cursor: string) => setHistory((previous) => [...previous, cursor]),
    previous:
      history.length > 1
        ? () => setHistory((previous) => previous.slice(0, -1))
        : undefined,
    reset: () => setHistory([undefined]),
  };
}
export function Pagination({
  page,
  next,
}: {
  page: ReturnType<typeof useCursor>;
  next?: string | null;
}) {
  const { t } = useTranslation();
  return (
    <div className={styles.pagination}>
      <Button size="sm" disabled={!page.previous} onClick={page.previous}>
        {t("Previous")}
      </Button>
      <Button
        size="sm"
        disabled={!next}
        onClick={() => next && page.next(next)}
      >
        {t("Next")}
      </Button>
    </div>
  );
}

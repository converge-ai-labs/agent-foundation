import { useMemo, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { CopyButton } from "../../shared/identity";
import { ContentChunk, useContentMeasurements } from "./content-window";
import { sourceChunks } from "./content-source";
import styles from "./traces.module.css";

export function ContentSequence({
  items,
  render,
}: {
  items: readonly unknown[];
  render: (item: unknown, index: number) => ReactNode;
}) {
  const heights = useContentMeasurements(items);
  return (
    <div className={styles.contentSequence}>
      {items.map((item, index) =>
        items.length <= 10 ? (
          render(item, index)
        ) : (
          <ContentChunk
            key={index}
            index={index}
            heights={heights}
            estimate={320}
          >
            {() => render(item, index)}
          </ContentChunk>
        ),
      )}
    </div>
  );
}

export function ScrollSource({ text }: { text: string }) {
  const { t } = useTranslation();
  const chunks = useMemo(() => sourceChunks(text), [text]);
  const heights = useContentMeasurements(text);
  return (
    <div>
      <div className={styles.sourceToolbar}>
        <span>
          {t("Plain text · {{total}} characters", { total: text.length })}
        </span>
        <CopyButton value={text} copyLabel={t("Copy full text")} />
      </div>
      <div className={styles.sourceText}>
        {chunks.map((chunk, index) => (
          <ContentChunk
            key={index}
            index={index}
            heights={heights}
            estimate={Math.ceil(chunk.length / 80) * 20}
          >
            {() => <pre className={styles.sourceChunk}>{chunk}</pre>}
          </ContentChunk>
        ))}
      </div>
    </div>
  );
}

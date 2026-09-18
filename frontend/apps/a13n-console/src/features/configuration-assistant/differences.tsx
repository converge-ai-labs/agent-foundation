import { Button, DisclosureSection, SegmentedControl } from "a13n-ui";
import { diffLines } from "diff";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import styles from "./differences.module.css";

type Change = Schema["ConfigurationDifference"];
type Line = { text: string; number: number };
type Block = { before: Line[]; after: Line[]; unchanged: boolean };
function display(
  value: Schema["JsonValue"],
  present: boolean,
  rawText: boolean,
) {
  if (!present) return "";
  return rawText && typeof value === "string" && value.length
    ? value
    : JSON.stringify(value, null, 2);
}
function lines(text: string) {
  if (!text) return [];
  const result = text.split("\n");
  if (result.at(-1) === "") result.pop();
  return result;
}
function blocks(before: string, after: string): Block[] {
  // Bound comparison work for large, completely replaced configurations.
  const parts = diffLines(before, after, { timeout: 50 }) ?? [
    { value: before, removed: true, added: false },
    { value: after, removed: false, added: true },
  ];
  let oldLine = 1,
    newLine = 1;
  const result: Block[] = [];
  for (const part of parts) {
    const text = lines(part.value);
    if (part.added) {
      const added = text.map((text) => ({ text, number: newLine++ }));
      const previous = result.at(-1);
      if (previous && !previous.unchanged && previous.after.length === 0)
        previous.after = added;
      else result.push({ before: [], after: added, unchanged: false });
    } else if (part.removed) {
      result.push({
        before: text.map((text) => ({ text, number: oldLine++ })),
        after: [],
        unchanged: false,
      });
    } else {
      result.push({
        before: text.map((text) => ({ text, number: oldLine++ })),
        after: text.map((text) => ({ text, number: newLine++ })),
        unchanged: true,
      });
    }
  }
  return result;
}
function LineView({
  line,
  kind,
}: {
  line: Line;
  kind: "added" | "removed" | "context";
}) {
  return (
    <div className={`${styles.line} ${styles[kind]}`}>
      <span className={styles.number} aria-hidden="true">
        {line.number}
      </span>
      <span className={styles.sign}>
        {kind === "added" ? "+" : kind === "removed" ? "−" : " "}
      </span>
      <code>{line.text || " "}</code>
    </div>
  );
}
function ChangeView({ change }: { change: Change }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const [split, setSplit] = useState(false);
  const rawText =
    (!change.before_present || typeof change.before === "string") &&
    (!change.after_present || typeof change.after === "string");
  const before = display(change.before, change.before_present, rawText);
  const after = display(change.after, change.after_present, rawText);
  const diff = useMemo(() => blocks(before, after), [before, after]);
  const added = diff.reduce(
    (count, block) => count + (block.unchanged ? 0 : block.after.length),
    0,
  );
  const removed = diff.reduce(
    (count, block) => count + (block.unchanged ? 0 : block.before.length),
    0,
  );
  const hasHidden = diff.some(
    (block) => block.unchanged && block.before.length > 8,
  );
  return (
    <section
      className={styles.change}
      aria-label={change.path.join(".") || t("Complete configuration")}
    >
      <div className={styles.heading}>
        <strong>{change.path.join(".") || t("Complete configuration")}</strong>
        <span className={styles.summary}>
          {!change.before_present && <span>{t("Added configuration")}</span>}
          {!change.after_present && <span>{t("Removed configuration")}</span>}
          <span className={styles.addCount} aria-label={t("Added lines")}>
            +{added}
          </span>
          <span className={styles.removeCount} aria-label={t("Removed lines")}>
            −{removed}
          </span>
        </span>
      </div>
      <div className={styles.toolbar}>
        <span>{t("Changes")}</span>
        <SegmentedControl
          className={styles.viewSwitch}
          label={t("Comparison layout")}
          value={split ? "split" : "unified"}
          onValueChange={(value) => setSplit(value === "split")}
          options={[
            { value: "unified", label: t("Unified diff") },
            { value: "split", label: t("Side-by-side diff") },
          ]}
        />
        {hasHidden && (
          <Button
            size="sm"
            variant="ghost"
            aria-expanded={expanded}
            onClick={() => setExpanded(!expanded)}
          >
            {t(expanded ? "Collapse unchanged lines" : "Show unchanged lines")}
          </Button>
        )}
      </div>
      <div className={`${styles.content} a13n-scrollbar`} data-split={split}>
        {split && (
          <div className={styles.columnLabels}>
            <span>{t("Before")}</span>
            <span>{t("After")}</span>
          </div>
        )}
        {diff.map((block, index) => {
          const hidden =
            !expanded && block.unchanged && block.before.length > 8;
          const renderRows = (start: number, end: number) => {
            if (split)
              return Array.from({ length: end - start }, (_, i) => {
                const offset = start + i;
                return (
                  <div className={styles.pair} key={offset}>
                    <div>
                      {block.before[offset] && (
                        <LineView
                          line={block.before[offset]}
                          kind={block.unchanged ? "context" : "removed"}
                        />
                      )}
                    </div>
                    <div>
                      {block.after[offset] && (
                        <LineView
                          line={block.after[offset]}
                          kind={block.unchanged ? "context" : "added"}
                        />
                      )}
                    </div>
                  </div>
                );
              });
            return (
              <>
                {!block.unchanged &&
                  block.before
                    .slice(start, end)
                    .map((line) => (
                      <LineView
                        key={`old-${line.number}`}
                        line={line}
                        kind="removed"
                      />
                    ))}
                {block.after.slice(start, end).map((line) => (
                  <LineView
                    key={`new-${line.number}`}
                    line={line}
                    kind={block.unchanged ? "context" : "added"}
                  />
                ))}
              </>
            );
          };
          const length = Math.max(block.before.length, block.after.length);
          return (
            <div key={index}>
              {hidden ? (
                <>
                  {renderRows(0, 3)}
                  <button
                    type="button"
                    className={styles.fold}
                    onClick={() => setExpanded(true)}
                  >
                    {t("Show unchanged lines")} · {length - 6}
                  </button>
                  {renderRows(length - 3, length)}
                </>
              ) : (
                renderRows(0, length)
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
export function Differences({
  title,
  changes,
}: {
  title: string;
  changes: Change[];
}) {
  const { t } = useTranslation();
  return (
    <DisclosureSection title={title} defaultOpen>
      {changes.length ? (
        changes.map((change) => (
          <ChangeView key={JSON.stringify(change)} change={change} />
        ))
      ) : (
        <p>{t("No changes")}</p>
      )}
    </DisclosureSection>
  );
}

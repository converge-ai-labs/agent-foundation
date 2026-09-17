import { Button, ToggleGroup, ToggleGroupItem } from "a13n-ui";
import { useId } from "react";
import type { Schema } from "../transport/client";
import styles from "./model-picker.module.css";

type Selection = Schema<"SubmitRequest">["thinking"];

export function thinkingSummary(
  model: Schema<"ModelSummary"> | undefined,
  value: Selection,
) {
  const control = model?.thinking;
  if (value != null) {
    const selected = control?.options.find((item) => item.value === value);
    return !selected || selected.disabled_reason
      ? "Unavailable thinking"
      : selected.label;
  }
  return control?.status === "supported" ? control.default_summary : undefined;
}

/** Provider rules and native values belong to the backend catalog, not React. */
export function ThinkingPicker({
  model,
  value,
  disabled,
  onChange,
}: {
  model?: Schema<"ModelSummary">;
  value?: Selection;
  disabled?: boolean;
  onChange: (value: Selection) => void;
}) {
  const descriptionId = useId();
  const control = model?.thinking;
  const options = control?.options ?? [];
  const explicit = options.filter((item) => item.value !== null);
  const useList = explicit.length > 4;
  const inherited = options.find((item) => item.value === null);
  const selected = options.find((item) => item.value === (value ?? null));
  const unavailable =
    value != null && (!selected || !!selected.disabled_reason);
  return (
    <section className={styles.thinking} aria-label="Thinking">
      <div className={styles.heading}>
        <span>Thinking</span>
        {inherited && (explicit.length > 0 || value != null) && (
          <Button
            variant="ghost"
            size="sm"
            aria-pressed={value == null}
            disabled={disabled || !!inherited.disabled_reason}
            title={inherited.disabled_reason ?? inherited.description}
            onClick={() => onChange(null)}
          >
            {value == null ? "Using default" : "Use default"}
          </Button>
        )}
      </div>
      {explicit.length > 0 && (
        <ToggleGroup
          aria-label="Thinking level"
          aria-describedby={descriptionId}
          className={useList ? styles.levelList : styles.levels}
          orientation={useList ? "vertical" : "horizontal"}
          value={value == null ? [] : [JSON.stringify(value)]}
          disabled={disabled}
          onValueChange={(keys) => {
            const option = explicit.find(
              (item) => JSON.stringify(item.value) === keys[0],
            );
            if (option && !option.disabled_reason) onChange(option.value);
          }}
        >
          {explicit.map((item) => (
            <ToggleGroupItem
              key={JSON.stringify(item.value)}
              value={JSON.stringify(item.value)}
              aria-label={item.label}
              disabled={!!item.disabled_reason}
              title={item.disabled_reason ?? item.description}
            >
              {item.label}
              {useList && item.description && <small>{item.description}</small>}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      )}
      <p id={descriptionId} className={styles.hint}>
        {unavailable
          ? (selected?.disabled_reason ??
            "Unavailable selection — use default.")
          : value == null && control
            ? `Model default: ${control.default_summary}`
            : selected?.description}
      </p>
      {explicit
        .filter((item) => item.disabled_reason)
        .map((item) => (
          <p key={JSON.stringify(item.value)} className={styles.hint}>
            {item.label}: {item.disabled_reason}
          </p>
        ))}
      {(!control || control.reason) && (
        <p className={styles.hint}>
          {control?.reason ?? "No thinking controls available."}
        </p>
      )}
    </section>
  );
}

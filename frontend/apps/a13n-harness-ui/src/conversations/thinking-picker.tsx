import { useId } from "react";
import { Button, ToggleGroup, ToggleGroupItem } from "a13n-ui";
import type { Schema } from "../transport/client";
import styles from "./composer-settings.module.css";

type Selection = Schema<"SubmitRequest">["thinking"];

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
  const inherited = options.find((item) => item.value === null);
  const selected = options.find((item) => item.value === (value ?? null));
  const effective = value ?? control?.default_value;
  const unavailable =
    value != null && (!selected || !!selected.disabled_reason);
  return (
    <section aria-label="Thinking">
      <div className={styles.controlHeading}>
        <span>Thinking</span>
        <Button
          variant="ghost"
          size="sm"
          aria-label="Use default thinking"
          aria-pressed={value == null}
          disabled={disabled || !!inherited?.disabled_reason}
          title={inherited?.disabled_reason ?? inherited?.description}
          onClick={() => onChange(null)}
        >
          {value == null ? "Using default" : "Use default"}
        </Button>
      </div>
      {explicit.length > 0 && (
        <ToggleGroup
          aria-label="Thinking level"
          aria-describedby={descriptionId}
          className={styles.levels}
          value={effective == null ? [] : [JSON.stringify(effective)]}
          disabled={disabled}
          onValueChange={(keys) => {
            // Clicking the inherited active level makes it an explicit choice.
            const key =
              keys[0] ??
              (effective == null ? undefined : JSON.stringify(effective));
            const option = explicit.find(
              (item) => JSON.stringify(item.value) === key,
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

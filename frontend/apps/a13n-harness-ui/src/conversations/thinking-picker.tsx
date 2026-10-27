import { useId } from "react";
import { Button, ToggleGroup, ToggleGroupItem } from "a13n-ui";
import { Check } from "@phosphor-icons/react";
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
  const sharedDescription =
    explicit.length > 1 &&
    explicit.every((item) => item.description === explicit[0]?.description)
      ? explicit[0]?.description
      : undefined;
  const selected = options.find((item) => item.value === (value ?? null));
  const unavailable =
    value != null && (!selected || !!selected.disabled_reason);
  return (
    <section aria-label="Thinking">
      <Button
        variant="ghost"
        className={styles.choice}
        aria-label="Use default thinking"
        aria-pressed={value == null}
        disabled={disabled || !!inherited?.disabled_reason}
        title={inherited?.disabled_reason ?? inherited?.description}
        onClick={() => onChange(null)}
      >
        <span>
          Model default
          <small>{control?.default_summary ?? inherited?.description}</small>
        </span>
        {value == null && <Check aria-hidden />}
      </Button>
      {explicit.length > 0 && (
        <ToggleGroup
          aria-label="Thinking level"
          aria-describedby={descriptionId}
          className={styles.levels}
          orientation="vertical"
          value={value == null ? [] : [JSON.stringify(value)]}
          disabled={disabled}
          onValueChange={(keys) => {
            // Re-selecting an explicit level keeps that choice.
            const key =
              keys[0] ?? (value == null ? undefined : JSON.stringify(value));
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
              <span>
                {item.label}
                {(item.disabled_reason || !sharedDescription) && (
                  <small>{item.disabled_reason ?? item.description}</small>
                )}
              </span>
              {item.value === value && <Check aria-hidden />}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      )}
      {(unavailable || sharedDescription) && (
        <p id={descriptionId} className={styles.hint}>
          {unavailable
            ? (selected?.disabled_reason ??
              "Unavailable selection — use default.")
            : sharedDescription}
        </p>
      )}
      {(!control || control.reason) && (
        <p className={styles.hint}>
          {control?.reason ?? "No thinking controls available."}
        </p>
      )}
    </section>
  );
}

import { useState } from "react";
import { ChoiceField } from "a13n-ui";
import styles from "../shell/workbench.module.css";

// Absence inherits; an empty list explicitly selects nothing. Choosing Custom
// opens the checklist without silently enabling the first installed resource.
export function SelectionField({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string[] | null | undefined;
  options: { value: string; label: string }[];
  onChange: (value: string[] | undefined) => void;
}) {
  const [editing, setEditing] = useState(false);
  const mode =
    value == null ? "default" : value.length || editing ? "custom" : "none";
  const selected = value ?? [];
  const choices = [
    ...options,
    ...selected
      .filter((id) => !options.some((option) => option.value === id))
      .map((id) => ({ value: id, label: `${id} (unavailable)` })),
  ];
  return (
    <div className={styles.stack}>
      <ChoiceField
        label={label}
        value={mode}
        options={[
          { value: "default", label: "Default (inherit)" },
          { value: "none", label: "None" },
          { value: "custom", label: "Custom selection" },
        ]}
        onValueChange={(mode) => {
          setEditing(mode === "custom");
          onChange(mode === "default" ? undefined : []);
        }}
      />
      {mode === "custom" && (
        <div className={styles.checks}>
          {!choices.length && (
            <p>No configured choices. Create a resource first.</p>
          )}
          {choices.map((option) => (
            <label key={option.value}>
              <input
                type="checkbox"
                checked={selected.includes(option.value)}
                onChange={(event) =>
                  onChange(
                    event.target.checked
                      ? [...selected, option.value]
                      : selected.filter((id) => id !== option.value),
                  )
                }
              />
              {option.label}
            </label>
          ))}
          {!selected.length && !!choices.length && (
            <small>No resources selected.</small>
          )}
        </div>
      )}
    </div>
  );
}

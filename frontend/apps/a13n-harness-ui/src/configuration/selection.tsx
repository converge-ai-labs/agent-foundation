import { useId, useState } from "react";
import { Checkbox, ChoiceField, FormField, Input, SettingsRow } from "a13n-ui";
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
  const id = useId();
  const [editing, setEditing] = useState(false);
  const [search, setSearch] = useState("");
  const mode =
    value == null ? "default" : value.length || editing ? "custom" : "none";
  const selected = value ?? [];
  const choices = [
    ...options,
    ...selected
      .filter((id) => !options.some((option) => option.value === id))
      .map((id) => ({ value: id, label: `${id} (unavailable)` })),
  ];
  const visible = choices.filter((option) =>
    `${option.label} ${option.value}`
      .toLocaleLowerCase()
      .includes(search.toLocaleLowerCase()),
  );
  return (
    <div>
      <SettingsRow
        label={label}
        controlId={id}
        description={
          mode === "custom"
            ? `${selected.length} selected · replaces the inherited list`
            : undefined
        }
      >
        <ChoiceField
          id={id}
          label={label}
          hideLabel
          className={styles.settingControl}
          aria-describedby={mode === "custom" ? `${id}-description` : undefined}
          value={mode}
          options={[
            { value: "default", label: "Use default" },
            { value: "none", label: "None" },
            { value: "custom", label: "Custom selection" },
          ]}
          onValueChange={(mode) => {
            setEditing(mode === "custom");
            setSearch("");
            onChange(
              mode === "default"
                ? undefined
                : mode === "custom"
                  ? selected
                  : [],
            );
          }}
        />
      </SettingsRow>
      {mode === "custom" && (
        <div
          className={styles.selectionOptions}
          role="group"
          aria-label={`${label} resources`}
        >
          {choices.length > 8 && (
            <FormField label={`Find ${label.toLowerCase()}`} hideLabel>
              <Input
                type="search"
                placeholder="Find resources…"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
            </FormField>
          )}
          <div className={`${styles.selectionList} a13n-scrollbar`}>
            {!choices.length && (
              <p>No configured choices. Create a resource first.</p>
            )}
            {!!choices.length && !visible.length && (
              <p>No matching resources.</p>
            )}
            {visible.map((option) => (
              <label key={option.value} className={styles.selectionOption}>
                <Checkbox
                  checked={selected.includes(option.value)}
                  onCheckedChange={(checked) =>
                    onChange(
                      checked
                        ? [...selected, option.value]
                        : selected.filter((id) => id !== option.value),
                    )
                  }
                />
                <span>{option.label}</span>
              </label>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

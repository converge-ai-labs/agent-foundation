import { useId, type ComponentProps, type ReactNode } from "react";
import { FormField, SearchPicker, SettingsRow } from "a13n-ui";
import styles from "../shell/workbench.module.css";

// Keep unresolved references visible without substituting a different resource.
// SearchPicker uses a plain select for short lists and search for longer lists.
export function ResourceChoice({
  label,
  value,
  options,
  onValueChange,
  description,
  placeholder = "Choose a resource",
  loading = false,
  row = false,
}: {
  label: string;
  value: string;
  options: ComponentProps<typeof SearchPicker>["groups"][number]["options"];
  onValueChange: (value: string) => void;
  description?: ReactNode;
  placeholder?: string;
  loading?: boolean;
  row?: boolean;
}) {
  const id = useId();
  const choices = Array.from(
    new Map(
      options.map((item) => [
        item.value,
        { ...item, keywords: [item.value, ...(item.keywords ?? [])] },
      ]),
    ).values(),
  );
  const unresolved = !!value && !choices.some((item) => item.value === value);
  const control = (
    <SearchPicker
      id={id}
      label={label}
      placeholder={
        choices.find((item) => item.value === value)?.label ?? placeholder
      }
      emptyMessage={loading ? "Loading resources…" : "No matching resources."}
      value={value}
      onValueChange={onValueChange}
      aria-describedby={description ? `${id}-description` : undefined}
      groups={[
        { label, options: choices },
        ...(unresolved
          ? [
              {
                label: "Current selection",
                options: [
                  {
                    value,
                    label: loading ? value : `${value} (unavailable)`,
                    disabled: true,
                  },
                ],
              },
            ]
          : []),
      ]}
    />
  );
  return row ? (
    <SettingsRow label={label} description={description} controlId={id}>
      <div className={styles.settingControl}>{control}</div>
    </SettingsRow>
  ) : (
    <FormField label={label} description={description}>
      {control}
    </FormField>
  );
}

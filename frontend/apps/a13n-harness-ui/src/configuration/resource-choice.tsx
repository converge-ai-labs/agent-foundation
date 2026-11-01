import { useTranslation } from "../i18n";
import { SettingsRow } from "../shell/settings";
import { useId, type ComponentProps, type ReactNode } from "react";
import { FormField, SearchPicker } from "a13n-ui";
import styles from "../shell/workbench.module.css";

// Keep unresolved references visible without substituting a different resource.
// SearchPicker uses a plain select for short lists and search for longer lists.
export function ResourceChoice({
  label,
  value,
  options,
  onValueChange,
  description,
  placeholder,
  loading = false,
  row = true,
  actions,
}: {
  label: string;
  value: string;
  options: ComponentProps<typeof SearchPicker>["groups"][number]["options"];
  onValueChange: (value: string) => void;
  description?: ReactNode;
  placeholder?: string;
  loading?: boolean;
  row?: boolean;
  actions?: ReactNode;
}) {
  const { t } = useTranslation();
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
        placeholder ??
        choices.find((item) => item.value === "")?.label ??
        t("Choose an option")
      }
      emptyMessage={
        loading ? t("Loading resources…") : t("No matching resources.")
      }
      value={value}
      disabled={
        !unresolved &&
        !choices.some((choice) => !choice.disabled && choice.value !== value)
      }
      onValueChange={onValueChange}
      aria-describedby={description ? `${id}-description` : undefined}
      groups={[
        { label, options: choices },
        ...(unresolved
          ? [
              {
                label: t("Current selection"),
                options: [
                  {
                    value,
                    label: loading
                      ? t("Loading selection…")
                      : t("{{value}} (unavailable)", { value }),
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
      <div
        className={`${styles.settingControl} ${actions ? styles.settingControlWithActions : ""}`}
      >
        {control}
        {actions}
      </div>
    </SettingsRow>
  ) : (
    <FormField label={label} description={description}>
      {control}
    </FormField>
  );
}

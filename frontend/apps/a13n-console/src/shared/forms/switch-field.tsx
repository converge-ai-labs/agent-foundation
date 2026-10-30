import { ReadOnlyField, SettingsRow, Switch } from "a13n-ui";
import { useId, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

/** A setting keeps its name and explanation together, opposite its switch. */
export function SwitchField({
  label,
  description,
  checked,
  onCheckedChange,
  disabled,
  readOnly,
}: {
  label: string;
  description?: ReactNode;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  disabled?: boolean;
  readOnly?: boolean;
}) {
  const id = useId();
  const { t } = useTranslation();
  if (readOnly) {
    return (
      <ReadOnlyField
        label={label}
        description={description}
        data-field-layout="setting"
      >
        {t(checked ? "On" : "Off")}
      </ReadOnlyField>
    );
  }
  return (
    <SettingsRow
      label={label}
      description={description}
      controlId={id}
      stackOnNarrow={false}
      className="w-full py-0"
    >
      <Switch
        id={id}
        checked={checked}
        onCheckedChange={onCheckedChange}
        disabled={disabled}
        aria-describedby={description ? `${id}-description` : undefined}
      />
    </SettingsRow>
  );
}

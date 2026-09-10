import { SettingsRow, Switch } from "a13n-ui";
import { useId } from "react";
import { useTranslation } from "react-i18next";

export function ProviderEnabled({
  checked,
  onCheckedChange,
  disabled = false,
}: {
  checked: boolean;
  onCheckedChange: (value: boolean) => void;
  disabled?: boolean;
}) {
  const id = useId();
  const { t } = useTranslation();
  return (
    <SettingsRow
      stackOnNarrow={false}
      label={t("Enabled")}
      description={t("Allow agents to use this provider.")}
      controlId={id}
    >
      <Switch
        id={id}
        aria-describedby={`${id}-description`}
        checked={checked}
        onCheckedChange={onCheckedChange}
        disabled={disabled}
      />
    </SettingsRow>
  );
}

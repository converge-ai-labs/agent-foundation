import { SettingsRow, Switch } from "a13n-ui";
import { useId } from "react";
import { useTranslation } from "react-i18next";

/** The enable switch every provider editor opens its settings group with. */
export function ProviderEnabled({
  checked,
  onCheckedChange,
  disabled = false,
  description,
}: {
  checked: boolean;
  onCheckedChange: (value: boolean) => void;
  disabled?: boolean;
  /** Stated only where disabling has a consequence worth naming. */
  description?: string;
}) {
  const id = useId();
  const { t } = useTranslation();
  return (
    <SettingsRow
      label={t("Enabled")}
      description={description}
      controlId={id}
      stackOnNarrow={false}
    >
      <Switch
        id={id}
        checked={checked}
        onCheckedChange={onCheckedChange}
        disabled={disabled}
      />
    </SettingsRow>
  );
}

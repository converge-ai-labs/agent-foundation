import { Switch } from "a13n-ui";
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
    <div className="flex items-center justify-between gap-4 py-1">
      <label htmlFor={id} className="text-sm">
        {t("Enabled")}
      </label>
      <Switch
        id={id}
        checked={checked}
        onCheckedChange={onCheckedChange}
        disabled={disabled}
      />
    </div>
  );
}

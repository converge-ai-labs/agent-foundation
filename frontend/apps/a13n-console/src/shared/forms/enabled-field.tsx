import { Switch } from "a13n-ui";
import { useTranslation } from "react-i18next";
import styles from "./forms.module.css";

/** Staged availability, placed opposite a configuration form's save actions. */
export function EnabledField({
  checked,
  onCheckedChange,
  disabled = false,
}: {
  checked: boolean;
  onCheckedChange: (value: boolean) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <label className={styles.inlineStatus}>
      {t("Enabled")}
      <Switch
        checked={checked}
        onCheckedChange={onCheckedChange}
        disabled={disabled}
      />
    </label>
  );
}

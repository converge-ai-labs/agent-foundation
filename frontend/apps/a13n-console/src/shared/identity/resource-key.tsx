import { resourceKeyPattern } from "../paths";
import { FormField, Input } from "a13n-ui";
import { useTranslation } from "react-i18next";

export function ResourceKeyField({
  value,
  onChange,
  disabled = false,
  readOnly = false,
}: {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  readOnly?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <FormField
      readOnly={readOnly}
      label={t("URL key")}
      description={t(
        "Lowercase letters, numbers, and hyphens. Changing this key invalidates existing links.",
      )}
    >
      <Input
        required
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
        pattern={resourceKeyPattern}
        maxLength={64}
      />
    </FormField>
  );
}

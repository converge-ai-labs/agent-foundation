import { FormField, Input } from "a13n-ui";
import { useTranslation } from "react-i18next";

/** The working directory a mount uses on an external target. */
export function WorkingDirectory({
  value,
  onChange,
}: {
  value: string;
  onChange: (path: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <FormField
      label={t("Working directory")}
      description={t(
        "Enter an absolute path on the external target. This is a working directory, not a filesystem sandbox.",
      )}
    >
      <Input value={value} onChange={(event) => onChange(event.target.value)} />
    </FormField>
  );
}

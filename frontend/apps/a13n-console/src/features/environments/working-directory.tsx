import { FormField, Input } from "a13n-ui";
import { useTranslation } from "react-i18next";

/** A conversation mount's directory in the selected environment's namespace. */
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
        "Use an existing absolute path inside this environment; leave empty for its default. For Local, /projects/app is inside the instance's root, not a path on your computer. Directories organize files; processes, ports and software remain shared.",
      )}
    >
      <Input value={value} onChange={(event) => onChange(event.target.value)} />
    </FormField>
  );
}

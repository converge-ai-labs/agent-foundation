import { useTranslation } from "../i18n";
import { SettingsChoice as ChoiceField } from "../shell/settings";
import type { Schema } from "../transport/client";

export type McpAuthority = NonNullable<Schema<"McpToolSettings">["authority"]>;

export function McpAuthorityField({
  label,
  value,
  disabled,
  onChange,
}: {
  label?: string;
  value?: string;
  disabled?: boolean;
  onChange: (value: McpAuthority) => void;
}) {
  const { t } = useTranslation();
  const options = [
    {
      value: "operator",
      label: t("System (default)"),
      detail: t(
        "Added as system instructions for direct connections. Does not change tool permissions.",
      ),
    },
    {
      value: "user",
      label: t("User context"),
      detail: t(
        "Passed as user-level context with lower priority than system instructions. Does not change tool permissions.",
      ),
    },
    {
      value: "none",
      label: t("Reference data"),
      detail: t(
        "Passed as passive reference data with no instruction authority. Does not change tool permissions.",
      ),
    },
  ] as const;
  const selected =
    options.find((option) => option.value === value) ?? options[0];
  return (
    <ChoiceField
      label={label ?? t("Server instructions")}
      description={selected.detail}
      value={selected.value}
      disabled={disabled}
      options={options}
      onValueChange={(next) => {
        const option = options.find(({ value }) => value === next);
        if (option) onChange(option.value);
      }}
    />
  );
}

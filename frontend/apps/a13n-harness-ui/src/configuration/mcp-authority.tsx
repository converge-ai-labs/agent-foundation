import { useTranslation } from "../i18n";
import { SettingsChoice as ChoiceField } from "../shell/settings";
import type { Schema } from "../transport/client";

export type McpAuthority = NonNullable<Schema<"McpToolSettings">["authority"]>;

export function McpAuthorityField({
  value,
  disabled,
  onChange,
}: {
  value?: string;
  disabled?: boolean;
  onChange: (value: McpAuthority) => void;
}) {
  const { t } = useTranslation();
  const options = [
    {
      value: "operator",
      label: t("Trusted (default)"),
      detail: t(
        "The model receives system instructions when this server is used directly.",
      ),
    },
    {
      value: "user",
      label: t("User-level"),
      detail: t("The model receives the server's text as user-level context."),
    },
    {
      value: "none",
      label: t("Untrusted data"),
      detail: t(
        "The model receives the server's text as data, not instructions.",
      ),
    },
  ] as const;
  const selected =
    options.find((option) => option.value === value) ?? options[0];
  return (
    <ChoiceField
      label={t("Server instruction trust")}
      description={`${selected.detail} ${t("Tool permissions are unchanged.")}`}
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

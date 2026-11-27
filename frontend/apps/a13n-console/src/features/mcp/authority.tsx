import { ChoiceField, SettingsRow } from "a13n-ui";
import { useId, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";

export type McpAuthority = NonNullable<Schema["McpConfig"]["authority"]>;

export function McpAuthorityRow({
  value = "operator",
  disabled,
  onChange,
  action,
}: {
  value?: McpAuthority;
  disabled?: boolean;
  onChange?: (value: McpAuthority) => void;
  action?: ReactNode;
}) {
  const { t } = useTranslation();
  const id = useId();
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
  const selected = options.find((option) => option.value === value)!;
  const label = t("Server instruction trust");
  return (
    <SettingsRow
      label={label}
      description={`${selected.detail} ${t("Tool permissions are unchanged.")}`}
      controlId={onChange ? id : undefined}
    >
      {onChange ? (
        <div className="flex flex-wrap items-center gap-2">
          <ChoiceField
            id={id}
            label={label}
            aria-describedby={`${id}-description`}
            hideLabel
            value={value}
            disabled={disabled}
            options={options}
            onValueChange={(next) => {
              const option = options.find(({ value }) => value === next);
              if (option) onChange(option.value);
            }}
          />
          {action}
        </div>
      ) : (
        selected.label
      )}
    </SettingsRow>
  );
}

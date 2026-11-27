import { ChoiceField, SettingsRow } from "a13n-ui";
import { useId, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";

export type McpAuthority = NonNullable<Schema["McpConfig"]["authority"]>;

export function useMcpAuthorityChoice(
  value: McpAuthority,
  onChange?: (value: McpAuthority) => void,
) {
  const { t } = useTranslation();
  const options = [
    {
      value: "operator",
      label: t("System (default)"),
      description: t(
        "Added as system instructions for direct connections. Does not change tool permissions.",
      ),
    },
    {
      value: "user",
      label: t("User context"),
      description: t(
        "Passed as user-level context with lower priority than system instructions. Does not change tool permissions.",
      ),
    },
    {
      value: "none",
      label: t("Reference data"),
      description: t(
        "Passed as passive reference data with no instruction authority. Does not change tool permissions.",
      ),
    },
  ] as const;
  const selected = options.find((option) => option.value === value)!;
  return {
    selected,
    field: {
      label: t("Server instructions"),
      description: selected.description,
      value,
      options,
      onValueChange: (next: string) => {
        const option = options.find(({ value }) => value === next);
        if (option) onChange?.(option.value);
      },
    },
  };
}

export function McpAuthorityRow({
  value,
  disabled,
  onChange,
  action,
}: {
  value: McpAuthority;
  disabled?: boolean;
  onChange?: (value: McpAuthority) => void;
  action?: ReactNode;
}) {
  const id = useId();
  const {
    selected,
    field: { description, ...choice },
  } = useMcpAuthorityChoice(value, onChange);
  return (
    <SettingsRow
      label={choice.label}
      description={description}
      controlId={onChange ? id : undefined}
    >
      {onChange ? (
        <div className="flex items-center gap-2">
          <ChoiceField
            {...choice}
            id={id}
            aria-describedby={`${id}-description`}
            hideLabel
            disabled={disabled}
            className="w-auto min-w-0 items-start"
          />
          {action}
        </div>
      ) : (
        selected.label
      )}
    </SettingsRow>
  );
}

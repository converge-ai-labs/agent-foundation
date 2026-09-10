import type { ReactNode } from "react";
import { FormField, SearchPicker } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { ProviderIcon } from "./provider-icon";

export function ProviderTypeField({
  definitions,
  value,
  onValueChange,
  disabled = false,
  labelAction,
}: {
  definitions: readonly { type: string; display_name: string }[];
  value: string;
  onValueChange: (value: string) => void;
  disabled?: boolean;
  labelAction?: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <FormField label={t("Provider type")} labelAction={labelAction}>
      <SearchPicker
        label={t("Provider type")}
        placeholder={t("Search providers…")}
        emptyMessage={t("No matching providers")}
        value={value}
        disabled={disabled}
        onValueChange={onValueChange}
        groups={[
          {
            label: "",
            options: definitions.map((item) => ({
              value: item.type,
              label: item.display_name,
              keywords: [item.type],
              icon: <ProviderIcon key={item.type} type={item.type} />,
            })),
          },
        ]}
      />
    </FormField>
  );
}

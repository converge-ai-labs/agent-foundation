import type { ReactNode } from "react";
import {
  FormField,
  ReadOnlyField,
  Select,
  SelectTrigger,
  SelectValue,
  SelectPopup,
  SelectItem,
} from "a13n-ui";
import { useTranslation } from "react-i18next";
import { ProviderIcon } from "./provider-icon";

export function ProviderTypeField({
  definitions,
  value,
  onValueChange,
  readOnly = false,
  labelAction,
}: {
  definitions: readonly { type: string; display_name: string }[];
  value: string;
  onValueChange: (value: string) => void;
  readOnly?: boolean;
  labelAction?: ReactNode;
}) {
  const { t } = useTranslation();
  if (readOnly)
    return (
      <ReadOnlyField label={t("Provider type")} description={labelAction}>
        <span className="flex items-center gap-2">
          <ProviderIcon type={value} />
          {definitions.find((item) => item.type === value)?.display_name ??
            value}
        </span>
      </ReadOnlyField>
    );
  return (
    <FormField label={t("Provider type")} labelAction={labelAction}>
      <Select
        value={value || null}
        items={definitions.map((item) => ({
          value: item.type,
          label: item.display_name,
        }))}
        onValueChange={(next) => {
          if (next !== null) onValueChange(next);
        }}
      >
        <SelectTrigger>
          <span className="flex items-center gap-2">
            {value && <ProviderIcon type={value} />}
            <SelectValue placeholder={t("Select provider type")} />
          </span>
        </SelectTrigger>
        <SelectPopup>
          {definitions.map((item) => (
            <SelectItem key={item.type} value={item.type}>
              <span className="flex items-center gap-2">
                <ProviderIcon type={item.type} />
                {item.display_name}
              </span>
            </SelectItem>
          ))}
        </SelectPopup>
      </Select>
    </FormField>
  );
}

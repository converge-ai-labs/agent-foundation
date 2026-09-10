import { useState, type ReactNode } from "react";

import {
  Combobox,
  ComboboxCollection,
  ComboboxEmpty,
  ComboboxGroup,
  ComboboxGroupLabel,
  ComboboxInput,
  ComboboxItem,
  ComboboxList,
  ComboboxPopup,
  ComboboxTrigger,
  ComboboxValue,
} from "../components/combobox";
import { SelectButton } from "../components/select";

export interface SearchOption {
  value: string;
  label: string;
  description?: string;
  icon?: ReactNode;
  keywords?: readonly string[];
  disabled?: boolean;
}
export interface SearchGroup {
  label: string;
  options: readonly SearchOption[];
}
type GroupedOption = SearchOption & { group: string };

export function SearchPicker({
  label,
  placeholder,
  emptyMessage,
  groups,
  value,
  onValueChange,
  disabled,
  id,
  "aria-describedby": describedBy,
}: {
  label: string;
  placeholder: string;
  emptyMessage: string;
  groups: readonly SearchGroup[];
  value?: string;
  onValueChange: (value: string) => void;
  disabled?: boolean;
  id?: string;
  "aria-describedby"?: string;
}) {
  const [query, setQuery] = useState("");
  const items = groups.map((group) => ({
    value: group.label,
    items: group.options.map((option) => ({ ...option, group: group.label })),
  }));
  const selected =
    items
      .flatMap((group) => group.items)
      .find((option) => option.value === value) ?? null;
  return (
    <Combobox
      autoHighlight
      items={items}
      value={selected}
      onValueChange={(next) => {
        if (next) onValueChange(next.value);
      }}
      disabled={disabled}
      inputValue={query}
      onInputValueChange={setQuery}
      onOpenChange={(open) => {
        if (!open) setQuery("");
      }}
      itemToStringLabel={(item) => item.label}
      itemToStringValue={(item) => item.value}
      isItemEqualToValue={(item, selected) => item.value === selected.value}
      filter={(item, search) =>
        [item.label, item.description, item.group, ...(item.keywords ?? [])]
          .filter(Boolean)
          .join(" ")
          .toLocaleLowerCase()
          .includes(search.toLocaleLowerCase())
      }
    >
      <ComboboxTrigger
        id={id}
        aria-label={label}
        aria-describedby={describedBy}
        render={<SelectButton />}
      >
        <span className="flex min-w-0 items-center gap-2">
          {selected?.icon}
          <ComboboxValue placeholder={placeholder} />
        </span>
      </ComboboxTrigger>
      <ComboboxPopup aria-label={label}>
        <div className="border-b p-2">
          <ComboboxInput
            aria-label={label}
            placeholder={placeholder}
            showTrigger={false}
          />
        </div>
        <ComboboxEmpty>{emptyMessage}</ComboboxEmpty>
        <ComboboxList>
          {(group: { value: string; items: GroupedOption[] }) => (
            <ComboboxGroup key={group.value} items={group.items}>
              {groups.length > 1 && (
                <ComboboxGroupLabel>{group.value}</ComboboxGroupLabel>
              )}
              <ComboboxCollection>
                {(item: GroupedOption) => (
                  <ComboboxItem
                    key={item.value}
                    value={item}
                    disabled={item.disabled}
                  >
                    <span className="flex min-w-0 items-center gap-2">
                      {item.icon}
                      <span className="min-w-0">
                        <span className="block">{item.label}</span>
                        {item.description && (
                          <span className="block text-xs text-muted-foreground">
                            {item.description}
                          </span>
                        )}
                      </span>
                    </span>
                  </ComboboxItem>
                )}
              </ComboboxCollection>
            </ComboboxGroup>
          )}
        </ComboboxList>
      </ComboboxPopup>
    </Combobox>
  );
}

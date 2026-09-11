import { MagnifyingGlassIcon } from "@phosphor-icons/react";
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
import { Badge } from "../components/badge";

export interface SearchOption {
  value: string;
  label: string;
  description?: string;
  badge?: string;
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
  onSearchChange,
  footer,
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
  onSearchChange?: (value: string) => void;
  footer?: ReactNode;
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
      onInputValueChange={(next) => {
        setQuery(next);
        onSearchChange?.(next);
      }}
      onOpenChange={(open) => {
        if (!open) {
          setQuery("");
          onSearchChange?.("");
        }
      }}
      itemToStringLabel={(item) => item.label}
      itemToStringValue={(item) => item.value}
      isItemEqualToValue={(item, selected) => item.value === selected.value}
      filter={(item, search) =>
        [
          item.label,
          item.badge,
          item.description,
          item.group,
          ...(item.keywords ?? []),
        ]
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
        <span className="flex w-full min-w-0 items-center gap-2">
          {selected?.icon}
          <span className="w-0 flex-1 truncate">
            <ComboboxValue placeholder={placeholder} />
          </span>
        </span>
      </ComboboxTrigger>
      <ComboboxPopup aria-label={label} className="w-(--anchor-width)">
        <div className="border-b px-1 py-1 focus-within:border-ring">
          <ComboboxInput
            className="w-full"
            aria-label={label}
            placeholder={placeholder}
            showTrigger={false}
            unstyled
            startAddon={<MagnifyingGlassIcon />}
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
                    aria-label={[item.label, item.badge, item.description]
                      .filter(Boolean)
                      .join(" ")}
                  >
                    <span className="flex w-full min-w-0 items-center gap-3">
                      {item.icon}
                      <span className="min-w-0 flex-1">
                        <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                          <span className="max-w-full break-words">
                            {item.label}
                          </span>
                          {item.badge && (
                            <Badge
                              variant="secondary"
                              size="sm"
                              className="max-w-full whitespace-normal break-words"
                            >
                              {item.badge}
                            </Badge>
                          )}
                        </span>
                        {item.description && (
                          <span className="mt-0.5 line-clamp-2 text-xs leading-relaxed text-muted-foreground">
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
        {footer && <div className="border-t p-2">{footer}</div>}
      </ComboboxPopup>
    </Combobox>
  );
}

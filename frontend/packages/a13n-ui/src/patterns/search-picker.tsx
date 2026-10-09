import { MagnifyingGlassIcon } from "@phosphor-icons/react";
import { useState, type ReactNode, type Ref } from "react";
import { cn } from "../lib/utils";

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
import { Badge } from "../components/badge";
import {
  Select,
  SelectButton,
  SelectTrigger,
  SelectValue,
  SelectPopup,
  SelectGroup,
  SelectGroupLabel,
  SelectItem,
} from "../components/select";

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
  open,
  onOpenChange,
  triggerRef,
  onSearchChange,
  footer,
  popupClassName,
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
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  triggerRef?: Ref<HTMLButtonElement>;
  onSearchChange?: (value: string) => void;
  footer?: ReactNode;
  popupClassName?: string;
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
  const options = items.flatMap((group) => group.items);
  if (!onSearchChange && options.length <= 8) {
    return (
      <Select
        open={open}
        onOpenChange={onOpenChange}
        items={options}
        value={value ?? null}
        onValueChange={(next) => {
          if (next !== null) onValueChange(next);
        }}
        disabled={disabled}
      >
        <SelectTrigger
          ref={triggerRef}
          id={id}
          aria-label={label}
          aria-describedby={describedBy}
        >
          <span className="flex min-w-0 items-center gap-2">
            {selected?.icon}
            <SelectValue placeholder={placeholder} />
          </span>
        </SelectTrigger>
        <SelectPopup
          aria-label={label}
          className={cn("w-(--anchor-width)", popupClassName)}
        >
          {groups.map((group) => (
            <SelectGroup key={group.label}>
              {groups.length > 1 && (
                <SelectGroupLabel>{group.label}</SelectGroupLabel>
              )}
              {group.options.map((option) => (
                <SelectItem
                  key={option.value}
                  value={option.value}
                  disabled={option.disabled}
                >
                  <OptionContent item={option} />
                </SelectItem>
              ))}
            </SelectGroup>
          ))}
          {options.length === 0 && (
            <p className="px-3 py-2 text-sm text-muted-foreground">
              {emptyMessage}
            </p>
          )}
          {footer && <div className="p-2">{footer}</div>}
        </SelectPopup>
      </Select>
    );
  }
  return (
    <Combobox
      open={open}
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
        onOpenChange?.(open);
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
        ref={triggerRef}
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
      <ComboboxPopup
        aria-label={label}
        className={cn("w-(--anchor-width)", popupClassName)}
      >
        <div className="px-1 py-1">
          <ComboboxInput
            className="w-full"
            aria-label={label}
            placeholder={placeholder}
            showTrigger={false}
            unstyled
            startAddon={<MagnifyingGlassIcon />}
          />
        </div>
        <div aria-hidden className="mx-3 border-b border-border/60" />
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
                    <OptionContent item={item} />
                  </ComboboxItem>
                )}
              </ComboboxCollection>
            </ComboboxGroup>
          )}
        </ComboboxList>
        {footer && <div className="p-2">{footer}</div>}
      </ComboboxPopup>
    </Combobox>
  );
}

function OptionContent({ item }: { item: SearchOption }) {
  return (
    <span className="flex w-full min-w-0 items-center gap-3">
      {item.icon}
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="max-w-full break-words">{item.label}</span>
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
  );
}

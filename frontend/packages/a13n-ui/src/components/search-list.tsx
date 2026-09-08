import type { ReactNode } from "react";
import { Command } from "cmdk";
import { Search, Check } from "lucide-react";
import { OptionContent } from "./option-content";
import { Kbd } from "./kbd";
import styles from "./search-list.module.css";
export interface SearchOption {
  value: string;
  label: string;
  description?: string;
  icon?: ReactNode;
  keywords?: readonly string[];
  disabled?: boolean;
  shortcut?: string;
}
export interface SearchGroup {
  label: string;
  options: readonly SearchOption[];
}
export interface SearchListProps {
  label: string;
  placeholder: string;
  emptyMessage: string;
  groups: readonly SearchGroup[];
  selectedValue?: string;
  onSelect: (value: string) => void;
}
export function SearchList({
  label,
  placeholder,
  emptyMessage,
  groups,
  selectedValue,
  onSelect,
}: SearchListProps) {
  return (
    <Command label={label} loop className={styles.root}>
      <div className={styles.search}>
        <Search size={16} aria-hidden="true" />
        <Command.Input
          aria-label={label}
          placeholder={placeholder}
          className={styles.input}
        />
      </div>
      <Command.List label={label} className={styles.list}>
        <Command.Empty className={styles.empty}>{emptyMessage}</Command.Empty>
        {groups.map((group) => (
          <Command.Group key={group.label} heading={group.label}>
            {group.options.map((option) => (
              <Command.Item
                key={option.value}
                value={option.value}
                keywords={[
                  option.label,
                  option.description ?? "",
                  group.label,
                  ...(option.keywords ?? []),
                ]}
                disabled={option.disabled}
                onSelect={() => onSelect(option.value)}
                className={styles.item}
              >
                <OptionContent
                  {...option}
                  trailing={
                    option.value === selectedValue ? (
                      <Check size={14} />
                    ) : option.shortcut ? (
                      <Kbd>{option.shortcut}</Kbd>
                    ) : undefined
                  }
                />
              </Command.Item>
            ))}
          </Command.Group>
        ))}
      </Command.List>
    </Command>
  );
}

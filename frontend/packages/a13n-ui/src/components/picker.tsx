import { useId, useState } from "react";
import * as Primitive from "@radix-ui/react-popover";
import { ChevronDown } from "lucide-react";
import { SearchList, type SearchListProps } from "./search-list";
import controlStyles from "./select.module.css";
import styles from "./picker.module.css";
export interface PickerProps extends Omit<
  SearchListProps,
  "selectedValue" | "onSelect"
> {
  value?: string;
  onValueChange: (value: string) => void;
  disabled?: boolean;
  id?: string;
  "aria-describedby"?: string;
  variant?: "default" | "ghost";
  size?: "sm" | "md";
}
export function Picker({
  value,
  onValueChange,
  disabled,
  id,
  variant = "default",
  size = "sm",
  "aria-describedby": describedBy,
  ...list
}: PickerProps) {
  const valueId = useId();
  const [open, setOpen] = useState(false);
  const selected = list.groups
    .flatMap((group) => group.options)
    .find((option) => option.value === value);
  return (
    <Primitive.Root open={open} onOpenChange={setOpen}>
      <Primitive.Trigger asChild>
        <button
          type="button"
          id={id}
          disabled={disabled}
          aria-label={list.label}
          aria-describedby={[valueId, describedBy].filter(Boolean).join(" ")}
          className={controlStyles.trigger}
          data-size={size}
          data-variant={variant}
        >
          {selected?.icon && (
            <span className={controlStyles.icon} aria-hidden="true">
              {selected.icon}
            </span>
          )}
          <span id={valueId}>{selected?.label ?? list.placeholder}</span>
          <ChevronDown
            className={controlStyles.chevron}
            size={14}
            aria-hidden="true"
          />
        </button>
      </Primitive.Trigger>
      <Primitive.Portal>
        <Primitive.Content
          aria-label={list.label}
          align="start"
          sideOffset={5}
          collisionPadding={8}
          className={styles.content}
        >
          <SearchList
            {...list}
            selectedValue={value}
            onSelect={(next) => {
              onValueChange(next);
              setOpen(false);
            }}
          />
        </Primitive.Content>
      </Primitive.Portal>
    </Primitive.Root>
  );
}

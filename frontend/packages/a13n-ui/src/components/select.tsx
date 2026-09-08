import type { ComponentProps, ReactNode } from "react";
import * as Primitive from "@radix-ui/react-select";
import { Check, ChevronDown, ChevronUp } from "lucide-react";
import styles from "./select.module.css";
export interface SelectOption {
  value: string;
  label: string;
  icon?: ReactNode;
  disabled?: boolean;
}
export type SelectProps = Omit<
  ComponentProps<typeof Primitive.Root>,
  "children"
> &
  Pick<
    ComponentProps<typeof Primitive.Trigger>,
    "id" | "ref" | "aria-describedby" | "aria-invalid" | "aria-labelledby"
  > & {
    label: string;
    placeholder: string;
    options: readonly SelectOption[];
    size?: "sm" | "md";
    variant?: "default" | "ghost";
    fullWidth?: boolean;
    className?: string;
  };
/** Standalone selection control. Use SelectField for a labeled form field. */
export function Select({
  label,
  placeholder,
  options,
  id,
  ref,
  size = "md",
  variant = "default",
  fullWidth = false,
  className = "",
  "aria-describedby": describedBy,
  "aria-invalid": invalid,
  "aria-labelledby": labelledBy,
  ...props
}: SelectProps) {
  return (
    <Primitive.Root {...props}>
      <Primitive.Trigger
        ref={ref}
        id={id}
        aria-label={labelledBy ? undefined : label}
        aria-labelledby={labelledBy}
        aria-describedby={describedBy}
        aria-invalid={invalid}
        data-size={size}
        data-variant={variant}
        data-full-width={fullWidth}
        className={`${styles.trigger} ${className}`}
      >
        <Primitive.Value placeholder={placeholder} />
        <Primitive.Icon className={styles.chevron}>
          <ChevronDown size={14} />
        </Primitive.Icon>
      </Primitive.Trigger>
      <Primitive.Portal>
        <Primitive.Content
          position="popper"
          sideOffset={5}
          collisionPadding={8}
          className={styles.content}
        >
          <Primitive.ScrollUpButton className={styles.scroll}>
            <ChevronUp size={14} />
          </Primitive.ScrollUpButton>
          <Primitive.Viewport>
            {options.map((option) => (
              <Primitive.Item
                key={option.value}
                value={option.value}
                disabled={option.disabled}
                textValue={option.label}
                className={styles.item}
              >
                <Primitive.ItemText>
                  <span className={styles.value}>
                    {option.icon && (
                      <span aria-hidden="true" className={styles.icon}>
                        {option.icon}
                      </span>
                    )}
                    {option.label}
                  </span>
                </Primitive.ItemText>
                <Primitive.ItemIndicator className={styles.indicator}>
                  <Check size={14} />
                </Primitive.ItemIndicator>
              </Primitive.Item>
            ))}
          </Primitive.Viewport>
          <Primitive.ScrollDownButton className={styles.scroll}>
            <ChevronDown size={14} />
          </Primitive.ScrollDownButton>
        </Primitive.Content>
      </Primitive.Portal>
    </Primitive.Root>
  );
}

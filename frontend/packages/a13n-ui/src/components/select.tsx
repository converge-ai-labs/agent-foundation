import { useId } from "react";
import type { ComponentProps, ReactNode } from "react";
import * as Primitive from "@radix-ui/react-select";
import { Check, ChevronDown, ChevronUp } from "lucide-react";
import { Field, fieldDescription } from "./field";
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
> & {
  label: string;
  placeholder: string;
  options: readonly SelectOption[];
  hint?: string;
  error?: string;
  id?: string;
  className?: string;
};
export function Select({
  label,
  placeholder,
  options,
  hint,
  error,
  id: providedId,
  className = "",
  ...props
}: SelectProps) {
  const generatedId = useId();
  const id = providedId ?? generatedId;
  return (
    <Field id={id} label={label} hint={hint} error={error}>
      <Primitive.Root {...props}>
        <Primitive.Trigger
          id={id}
          aria-invalid={error ? true : undefined}
          aria-describedby={fieldDescription(id, hint, error)}
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
    </Field>
  );
}

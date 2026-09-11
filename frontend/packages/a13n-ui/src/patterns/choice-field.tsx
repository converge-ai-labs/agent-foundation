import type { ReactNode } from "react";
import { ReadOnlyField } from "./read-only-field";
import { FormField } from "./form-field";
import {
  Select,
  SelectGroup,
  SelectItem,
  SelectPopup,
  SelectTrigger,
  SelectValue,
} from "../components/select";

export interface ChoiceOption {
  value: string;
  label: string;
  icon?: ReactNode;
  disabled?: boolean;
}
export function ChoiceField({
  label,
  placeholder,
  options,
  value,
  onValueChange,
  disabled,
  readOnly,
  required,
  hideLabel,
  variant = "default",
  description,
  error,
  id,
  className,
  "aria-describedby": describedBy,
}: {
  label: string;
  placeholder?: string;
  options: readonly ChoiceOption[];
  value?: string;
  onValueChange?: (value: string) => void;
  disabled?: boolean;
  readOnly?: boolean;
  required?: boolean;
  hideLabel?: boolean;
  variant?: "default" | "filter";
  description?: ReactNode;
  error?: ReactNode;
  id?: string;
  className?: string;
  "aria-describedby"?: string;
}) {
  if (readOnly) {
    const selected = options.find((option) => option.value === value);
    return (
      <ReadOnlyField
        label={label}
        hideLabel={hideLabel}
        description={description}
        className={className}
      >
        <span className="flex items-center gap-2">
          {selected?.icon}
          {selected?.label ?? value ?? "—"}
        </span>
      </ReadOnlyField>
    );
  }
  return (
    <FormField
      label={label}
      hideLabel={hideLabel || variant === "filter"}
      description={description}
      error={error}
      className={
        className ??
        (variant === "filter" ? "w-auto min-w-0 max-w-full" : undefined)
      }
    >
      <ChoiceControl
        id={id}
        aria-describedby={describedBy}
        disabled={disabled}
        options={options}
        value={value}
        onValueChange={onValueChange}
        required={required}
        placeholder={placeholder}
        inlineLabel={variant === "filter" ? label : undefined}
      />
    </FormField>
  );
}

function ChoiceControl({
  id,
  options,
  value,
  onValueChange,
  disabled,
  required,
  placeholder,
  inlineLabel,
  ...props
}: Pick<
  React.ComponentProps<typeof ChoiceField>,
  | "id"
  | "options"
  | "value"
  | "onValueChange"
  | "disabled"
  | "required"
  | "placeholder"
  | "aria-describedby"
> & { "aria-invalid"?: boolean; inlineLabel?: string }) {
  return (
    <Select
      items={options}
      value={value ?? null}
      onValueChange={(next) => {
        if (next !== null) onValueChange?.(next);
      }}
      disabled={disabled}
      required={required}
    >
      <SelectTrigger id={id} {...props} className="w-full">
        {inlineLabel && (
          <span aria-hidden="true" className="shrink-0 text-muted-foreground">
            {inlineLabel}
          </span>
        )}
        <SelectValue placeholder={placeholder} />
      </SelectTrigger>
      <SelectPopup>
        <SelectGroup>
          {options.map((option) => (
            <SelectItem
              key={option.value}
              value={option.value}
              disabled={option.disabled}
            >
              <span className="flex items-center gap-2">
                {option.icon}
                {option.label}
              </span>
            </SelectItem>
          ))}
        </SelectGroup>
      </SelectPopup>
    </Select>
  );
}

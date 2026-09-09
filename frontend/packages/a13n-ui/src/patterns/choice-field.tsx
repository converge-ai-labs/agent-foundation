import type { ReactNode } from "react";
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
  required,
  hideLabel,
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
  required?: boolean;
  hideLabel?: boolean;
  description?: ReactNode;
  error?: ReactNode;
  id?: string;
  className?: string;
  "aria-describedby"?: string;
}) {
  return (
    <FormField
      label={label}
      hideLabel={hideLabel}
      description={description}
      error={error}
      className={className}
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
> & { "aria-invalid"?: boolean }) {
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

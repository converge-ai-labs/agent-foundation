import type { ReactNode } from "react";
import { ReadOnlyField } from "./read-only-field";
import { FormField } from "./form-field";
import {
  Select,
  SelectGroup,
  SelectGroupLabel,
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
  group?: string;
  hint?: string;
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
  popupClassName,
  "aria-describedby": describedBy,
  "data-field-layout": layout,
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
  popupClassName?: string;
  "aria-describedby"?: string;
  "data-field-layout"?: React.ComponentProps<
    typeof FormField
  >["data-field-layout"];
}) {
  if (readOnly) {
    const selected = options.find((option) => option.value === value);
    return (
      <ReadOnlyField
        data-field-kind="choice"
        data-field-layout={layout}
        label={label}
        hideLabel={hideLabel}
        description={description}
        className={className}
      >
        <span className="flex items-center gap-2">
          {selected?.icon}
          {selected?.label ?? value ?? "—"}
          {selected?.hint && (
            <span className="text-muted-foreground">{selected.hint}</span>
          )}
        </span>
      </ReadOnlyField>
    );
  }
  return (
    <FormField
      data-field-layout={layout}
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
        popupClassName={popupClassName}
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
  popupClassName,
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
  | "popupClassName"
  | "aria-describedby"
> & { "aria-invalid"?: boolean; inlineLabel?: string }) {
  const selected = options.find((option) => option.value === value);
  const groups = [...new Set(options.map((option) => option.group))];
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
        {options.find((option) => option.value === value)?.icon}
        <SelectValue placeholder={placeholder} />
        {selected?.hint && (
          <span className="shrink-0 text-xs text-muted-foreground">
            {selected.hint}
          </span>
        )}
      </SelectTrigger>
      <SelectPopup className={popupClassName}>
        {groups.map((group) => (
          <SelectGroup key={group ?? "ungrouped"}>
            {group && <SelectGroupLabel>{group}</SelectGroupLabel>}
            {options
              .filter((option) => option.group === group)
              .map((option) => (
                <SelectItem
                  key={option.value}
                  value={option.value}
                  disabled={option.disabled}
                  aria-label={
                    option.hint ? `${option.label} ${option.hint}` : undefined
                  }
                >
                  <span className="flex min-w-0 items-center gap-3">
                    {option.icon}
                    <span
                      data-slot="choice-option-label"
                      className="min-w-0 flex-1 truncate"
                    >
                      {option.label}
                    </span>
                    {option.hint && (
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {option.hint}
                      </span>
                    )}
                  </span>
                </SelectItem>
              ))}
          </SelectGroup>
        ))}
      </SelectPopup>
    </Select>
  );
}

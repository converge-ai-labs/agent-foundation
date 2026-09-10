import {
  cloneElement,
  useId,
  type ComponentProps,
  type ReactElement,
  type ReactNode,
} from "react";
import {
  Field,
  FieldDescription,
  FieldError,
  FieldLabel,
} from "../components/field";

type ControlProps = {
  id?: string;
  disabled?: boolean;
  "aria-describedby"?: string;
  "aria-invalid"?: boolean;
};

/** Associates one control with its label, hint, and validation message. */
export function FormField({
  label,
  labelAction,
  description,
  error,
  hideLabel,
  disabled,
  children,
  className,
  ...props
}: Omit<ComponentProps<typeof Field>, "children"> & {
  label: ReactNode;
  labelAction?: ReactNode;
  description?: ReactNode;
  error?: ReactNode;
  hideLabel?: boolean;
  disabled?: boolean;
  children: ReactElement<ControlProps>;
}) {
  const generatedId = useId();
  const id = children.props.id ?? generatedId;
  const describedBy =
    [
      children.props["aria-describedby"],
      description && `${id}-description`,
      error && `${id}-error`,
    ]
      .filter(Boolean)
      .join(" ") || undefined;
  return (
    <Field
      {...props}
      invalid={!!error || props.invalid}
      disabled={disabled || children.props.disabled}
      className={className ?? "w-full min-w-0"}
    >
      {labelAction ? (
        <div className="flex w-full flex-wrap items-center justify-between gap-x-4 gap-y-1">
          <FieldLabel
            htmlFor={id}
            className={hideLabel ? "sr-only" : undefined}
          >
            {label}
          </FieldLabel>
          {labelAction}
        </div>
      ) : (
        <FieldLabel htmlFor={id} className={hideLabel ? "sr-only" : undefined}>
          {label}
        </FieldLabel>
      )}
      {cloneElement(children, {
        id,
        disabled: disabled || children.props.disabled,
        "aria-describedby": describedBy,
        "aria-invalid": !!error || children.props["aria-invalid"],
      })}
      {description && (
        <FieldDescription id={`${id}-description`}>
          {description}
        </FieldDescription>
      )}
      {error && (
        <FieldError match={true} id={`${id}-error`}>
          {error}
        </FieldError>
      )}
    </Field>
  );
}

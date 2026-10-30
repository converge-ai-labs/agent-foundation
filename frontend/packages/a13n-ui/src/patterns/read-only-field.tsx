import { useId, type ComponentProps, type ReactNode } from "react";
import { cn } from "../lib/utils";

/** Displays a labeled value without suggesting that it can be edited. */
export function ReadOnlyField({
  label,
  children,
  description,
  className,
  hideLabel,
  ...props
}: Omit<ComponentProps<"div">, "children"> & {
  label: ReactNode;
  children: ReactNode;
  description?: ReactNode;
  className?: string;
  hideLabel?: boolean;
}) {
  const id = useId();
  return (
    <div
      {...props}
      data-slot="read-only-field"
      role="group"
      aria-labelledby={id}
      className={cn("flex min-w-0 w-full flex-col gap-2", className)}
    >
      <div
        data-slot="read-only-label"
        id={id}
        className={hideLabel ? "sr-only" : "text-sm font-medium"}
      >
        {label}
      </div>
      <div
        data-slot="read-only-value"
        className="min-w-0 select-text whitespace-pre-wrap wrap-anywhere text-sm leading-6"
      >
        {children}
      </div>
      {description && (
        <p
          data-slot="field-description"
          className="text-muted-foreground text-xs leading-relaxed"
        >
          {description}
        </p>
      )}
    </div>
  );
}

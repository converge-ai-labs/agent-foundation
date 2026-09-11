import { useId, type ReactNode } from "react";
import { cn } from "../lib/utils";

/** Displays a labeled value without suggesting that it can be edited. */
export function ReadOnlyField({
  label,
  children,
  description,
  className,
  hideLabel,
}: {
  label: ReactNode;
  children: ReactNode;
  description?: ReactNode;
  className?: string;
  hideLabel?: boolean;
}) {
  const id = useId();
  return (
    <div
      role="group"
      aria-labelledby={id}
      className={cn("flex min-w-0 w-full flex-col gap-2", className)}
    >
      <div id={id} className={hideLabel ? "sr-only" : "text-sm font-medium"}>
        {label}
      </div>
      <div className="min-w-0 select-text whitespace-pre-wrap wrap-anywhere text-sm leading-6">
        {children}
      </div>
      {description && (
        <p className="text-muted-foreground text-sm leading-normal">
          {description}
        </p>
      )}
    </div>
  );
}

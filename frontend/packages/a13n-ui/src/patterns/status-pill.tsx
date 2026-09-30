import type React from "react";
import { cn } from "../lib/utils";

export type StatusPillVariant =
  "success" | "warning" | "danger" | "info" | "neutral";

/**
 * Settled states (success, neutral) are a dot and a label with no tint, so the
 * states that ask for attention are the only tinted pills on a screen.
 */
const variantClassNames: Record<StatusPillVariant, string> = {
  danger: "bg-destructive/10 px-2 text-destructive-foreground",
  info: "bg-info/10 px-2 text-info-foreground",
  neutral: "text-muted-foreground",
  success: "text-foreground/80",
  warning: "bg-warning/12 px-2 text-warning-foreground",
};

const dotClassNames: Record<StatusPillVariant, string> = {
  danger: "bg-destructive",
  info: "bg-info",
  neutral: "bg-muted-foreground/60",
  success: "bg-success",
  warning: "bg-warning",
};

export interface StatusPillProps extends Omit<
  React.ComponentProps<"span">,
  "children"
> {
  variant?: StatusPillVariant;
  children: React.ReactNode;
}

/** Status is a 6px dot plus a 12px medium label; attention states add a tint of the same hue. */
export function StatusPill({
  variant = "neutral",
  className,
  children,
  ...props
}: StatusPillProps): React.ReactElement {
  return (
    <span
      className={cn(
        "inline-flex min-w-0 shrink-0 items-center gap-1.5 rounded-full py-[3px] font-medium text-[12px] leading-4",
        variantClassNames[variant],
        className,
      )}
      data-slot="status-pill"
      data-variant={variant}
      {...props}
    >
      <span
        aria-hidden="true"
        className={cn("size-1.5 shrink-0 rounded-full", dotClassNames[variant])}
      />
      <span className="min-w-0 truncate">{children}</span>
    </span>
  );
}

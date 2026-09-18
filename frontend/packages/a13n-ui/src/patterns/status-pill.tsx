import type React from "react";
import { cn } from "../lib/utils";

export type StatusPillVariant =
  "success" | "warning" | "danger" | "info" | "neutral";

/** Tint and dot colour per semantic hue; the label always names the state. */
const variantClassNames: Record<StatusPillVariant, string> = {
  danger: "bg-destructive/10 text-destructive-foreground",
  info: "bg-info/10 text-info-foreground",
  neutral: "bg-foreground/8 text-muted-foreground",
  success: "bg-success/10 text-success-foreground",
  warning: "bg-warning/10 text-warning-foreground",
};

const dotClassNames: Record<StatusPillVariant, string> = {
  danger: "bg-destructive",
  info: "bg-info",
  neutral: "bg-muted-foreground/72",
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

/** Status is a 6px dot plus a 12px medium label on a 10% tint of the same hue. */
export function StatusPill({
  variant = "neutral",
  className,
  children,
  ...props
}: StatusPillProps): React.ReactElement {
  return (
    <span
      className={cn(
        "inline-flex min-w-0 shrink-0 items-center gap-1.5 rounded-full px-2 py-[3px] font-medium text-[12px] leading-4",
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

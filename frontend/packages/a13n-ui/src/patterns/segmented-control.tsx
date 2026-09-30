import type React from "react";
import type { ReactNode } from "react";
import { cn } from "../lib/utils";
import { ToggleGroup, ToggleGroupItem } from "../components/toggle-group";

export interface SegmentedControlOption {
  value: string;
  label: ReactNode;
  disabled?: boolean;
}

export interface SegmentedControlProps {
  /** Accessible name for the group; it is never rendered. */
  label: string;
  value: string;
  onValueChange: (value: string) => void;
  options: readonly SegmentedControlOption[];
  size?: "sm" | "default";
  className?: string;
}

/**
 * Exclusive switch for an in-place view or mode change: a track on the soft
 * surface with the active segment lifted onto the elevated surface. Quieter
 * than tabs, and it never reads as page navigation.
 */
export function SegmentedControl({
  label,
  value,
  onValueChange,
  options,
  size = "sm",
  className,
}: SegmentedControlProps): React.ReactElement {
  return (
    <ToggleGroup
      aria-label={label}
      data-slot="segmented-control"
      className={cn(
        "w-fit shrink-0 items-center gap-0.5 rounded-[8px] bg-muted p-0.5",
        className,
      )}
      value={[value]}
      onValueChange={(next) => {
        // Pressing the active segment must not leave the control unset.
        if (next[0]) onValueChange(next[0]);
      }}
    >
      {options.map((option) => (
        <ToggleGroupItem
          key={option.value}
          value={option.value}
          disabled={option.disabled}
          className={cn(
            "min-w-0 rounded-[6px] border-transparent bg-transparent font-medium text-muted-foreground shadow-none hover:bg-transparent hover:text-foreground data-pressed:bg-card data-pressed:text-foreground data-pressed:shadow-xs/10 dark:data-pressed:bg-card",
            // The toggle's responsive sizes must be overridden at every breakpoint.
            size === "sm"
              ? "h-6 min-w-0 px-2.5 text-xs sm:h-6 sm:min-w-0 sm:text-xs"
              : "h-7 min-w-0 px-3 text-sm sm:h-7 sm:min-w-0 sm:text-sm",
          )}
        >
          {option.label}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}

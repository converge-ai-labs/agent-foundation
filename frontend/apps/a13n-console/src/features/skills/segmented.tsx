import { ToggleGroup, ToggleGroupItem } from "a13n-ui";
import type { ReactNode } from "react";
import styles from "./skills.module.css";

export interface Segment {
  value: string;
  label: ReactNode;
  disabled?: boolean;
}

/**
 * Small exclusive switch for an in-place view or mode change: quieter than
 * tabs, and it never reads as page navigation.
 *
 * Local to skills until a second feature needs it; it belongs in `shared/`.
 */
export function SegmentedControl({
  label,
  value,
  onValueChange,
  segments,
  className,
}: {
  label: string;
  value: string;
  onValueChange: (value: string) => void;
  segments: readonly Segment[];
  className?: string;
}) {
  return (
    <ToggleGroup
      aria-label={label}
      className={`${styles.segmented} ${className ?? ""}`}
      value={[value]}
      onValueChange={(next) => {
        // Pressing the active segment must not leave the control unset.
        if (next[0]) onValueChange(next[0]);
      }}
    >
      {segments.map((segment) => (
        <ToggleGroupItem
          key={segment.value}
          value={segment.value}
          disabled={segment.disabled}
          className={styles.segment}
        >
          {segment.label}
        </ToggleGroupItem>
      ))}
    </ToggleGroup>
  );
}

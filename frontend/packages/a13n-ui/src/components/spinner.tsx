import { CircleNotchIcon } from "@phosphor-icons/react";
import type React from "react";
import { cn } from "../lib/utils";

export function Spinner({
  className,
  ...props
}: React.ComponentProps<typeof CircleNotchIcon>): React.ReactElement {
  return (
    <CircleNotchIcon
      aria-label="Loading"
      className={cn("animate-spin", className)}
      role="status"
      {...props}
    />
  );
}

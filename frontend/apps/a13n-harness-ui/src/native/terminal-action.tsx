import type { ComponentProps } from "react";
import { Button, Tooltip, TooltipPopup, TooltipTrigger } from "a13n-ui";

/** Terminal toolbar actions keep their names available without text chrome. */
export function TerminalAction({
  label,
  shortcut,
  description,
  children,
  ...props
}: Omit<ComponentProps<typeof Button>, "size" | "variant"> & {
  label: string;
  shortcut?: string;
  description?: string;
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label={label}
            {...props}
          />
        }
      >
        {children}
      </TooltipTrigger>
      <TooltipPopup>
        {label}
        {shortcut && ` (${shortcut})`}
        {description && ` · ${description}`}
      </TooltipPopup>
    </Tooltip>
  );
}

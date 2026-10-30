import { Button, Tooltip, TooltipTrigger, TooltipPopup } from "a13n-ui";
import { Question } from "@phosphor-icons/react";
import type { ReactNode } from "react";

export function HelpTip({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Button
            tooltip={false}
            variant="ghost"
            size="icon-sm"
            className="size-6 shrink-0 text-muted-foreground"
          />
        }
        aria-label={label}
      >
        <Question size={14} />
      </TooltipTrigger>
      <TooltipPopup>{children}</TooltipPopup>
    </Tooltip>
  );
}

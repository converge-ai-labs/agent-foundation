import { Button, Tooltip, TooltipTrigger, TooltipPopup } from "a13n-ui";
import { Question } from "@phosphor-icons/react";
import { useState, type ReactNode } from "react";

export function HelpTip({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <Tooltip open={open} onOpenChange={setOpen}>
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
        onClick={() => setOpen(true)}
      >
        <Question size={14} />
      </TooltipTrigger>
      <TooltipPopup>{children}</TooltipPopup>
    </Tooltip>
  );
}

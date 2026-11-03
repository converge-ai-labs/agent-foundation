import { QuestionIcon } from "@phosphor-icons/react";
import { Tooltip, TooltipPopup, TooltipTrigger } from "a13n-ui";
import type { ReactNode } from "react";

/** Supplementary field guidance, available on hover and keyboard focus. */
export function FieldHelp({
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
          <button
            type="button"
            aria-label={label}
            className="inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-2 focus-visible:outline-ring"
          />
        }
      >
        <QuestionIcon size={15} aria-hidden="true" />
      </TooltipTrigger>
      <TooltipPopup>{children}</TooltipPopup>
    </Tooltip>
  );
}

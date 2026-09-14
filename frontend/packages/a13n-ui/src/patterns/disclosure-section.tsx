import type { ComponentProps, ReactNode } from "react";
import { CaretRightIcon } from "@phosphor-icons/react";
import { Button } from "../components/button";
import {
  Collapsible,
  CollapsiblePanel,
  CollapsibleTrigger,
} from "../components/collapsible";

export function DisclosureSection({
  title,
  summary,
  children,
  ...props
}: Omit<ComponentProps<typeof Collapsible>, "title"> & {
  title: ReactNode;
  summary?: ReactNode;
}) {
  return (
    <Collapsible {...props}>
      <div className="rounded-lg bg-muted/50" data-slot="disclosure-surface">
        <CollapsibleTrigger
          className="group/disclosure-trigger h-auto min-h-9 w-full justify-start gap-2 px-3 py-2 text-left whitespace-normal"
          render={<Button type="button" variant="ghost" />}
        >
          <CaretRightIcon
            aria-hidden="true"
            className="size-3.5 text-muted-foreground transition-transform group-aria-expanded/disclosure-trigger:rotate-90"
          />
          <span className="min-w-0 flex-1">{title}</span>{" "}
          {summary && (
            <span className="min-w-0 text-right text-xs font-normal text-muted-foreground">
              {summary}
            </span>
          )}
        </CollapsibleTrigger>
        <CollapsiblePanel>
          <div className="grid gap-4 px-4 pt-2 pb-4">{children}</div>
        </CollapsiblePanel>
      </div>
    </Collapsible>
  );
}

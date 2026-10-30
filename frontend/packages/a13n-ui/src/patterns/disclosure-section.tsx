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
  // A quiet disclosure: no surface of its own, so its fields keep the form's
  // edges and a nested one never stacks a second fill. The trigger's hover
  // fill reaches into the gutter so its caret stands on the field edge.
  return (
    <Collapsible {...props}>
      <div data-slot="disclosure-surface">
        <CollapsibleTrigger
          className="group/disclosure-trigger -mx-2 h-auto min-h-8 w-[calc(100%+--spacing(4))] justify-start gap-2 px-2 py-1.5 text-left whitespace-normal"
          render={<Button type="button" variant="ghost" />}
        >
          <CaretRightIcon
            aria-hidden="true"
            className="size-3.5 text-muted-foreground transition-transform duration-150 ease-out motion-reduce:transition-none group-aria-expanded/disclosure-trigger:rotate-90"
          />
          <span className="min-w-0 flex-1">{title}</span>{" "}
          {summary && (
            <span className="min-w-0 text-right text-xs font-normal text-muted-foreground">
              {summary}
            </span>
          )}
        </CollapsibleTrigger>
        <CollapsiblePanel>
          <div className="grid gap-4 pt-3 pb-1">{children}</div>
        </CollapsiblePanel>
      </div>
    </Collapsible>
  );
}

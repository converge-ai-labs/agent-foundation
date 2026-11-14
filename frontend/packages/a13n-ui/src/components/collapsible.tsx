"use client";

import { Collapsible as CollapsiblePrimitive } from "@base-ui/react/collapsible";
import type React from "react";
import { cn } from "../lib/utils";

export function Collapsible({
  ...props
}: CollapsiblePrimitive.Root.Props): React.ReactElement {
  return <CollapsiblePrimitive.Root data-slot="collapsible" {...props} />;
}

export function CollapsibleTrigger({
  className,
  ...props
}: CollapsiblePrimitive.Trigger.Props): React.ReactElement {
  return (
    <CollapsiblePrimitive.Trigger
      className={className}
      data-slot="collapsible-trigger"
      {...props}
    />
  );
}

export function CollapsiblePanel({
  className,
  children,
  ...props
}: CollapsiblePrimitive.Panel.Props): React.ReactElement {
  return (
    <CollapsiblePrimitive.Panel
      className={cn(
        "grid grid-rows-[1fr] transition-[grid-template-rows,opacity] duration-(--a13n-duration-base) ease-(--a13n-ease-out) data-ending-style:grid-rows-[0fr] data-starting-style:grid-rows-[0fr] data-ending-style:opacity-0 data-starting-style:opacity-0 data-ending-style:duration-(--a13n-duration-fast) data-ending-style:ease-(--a13n-ease-in)",
        className,
      )}
      data-slot="collapsible-panel"
      {...props}
    >
      <div className="min-h-0 overflow-hidden">{children}</div>
    </CollapsiblePrimitive.Panel>
  );
}

export { CollapsiblePrimitive, CollapsiblePanel as CollapsibleContent };

import type { ComponentProps, ReactNode } from "react";
import { ChevronDown } from "lucide-react";
import { Button } from "../components/button";
import {
  Collapsible,
  CollapsiblePanel,
  CollapsibleTrigger,
} from "../components/collapsible";

export function DisclosureSection({
  title,
  children,
  ...props
}: Omit<ComponentProps<typeof Collapsible>, "title"> & { title: ReactNode }) {
  return (
    <Collapsible {...props}>
      <CollapsibleTrigger
        className="group/disclosure-trigger"
        render={
          <Button
            variant="ghost"
            className="w-full justify-between text-left"
          />
        }
      >
        {title}
        <ChevronDown
          aria-hidden="true"
          className="transition-transform group-data-open/disclosure-trigger:rotate-180"
        />
      </CollapsibleTrigger>
      <CollapsiblePanel>
        <div className="pt-3">{children}</div>
      </CollapsiblePanel>
    </Collapsible>
  );
}

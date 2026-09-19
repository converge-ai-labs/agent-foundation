"use client";

import { Tabs as TabsPrimitive } from "@base-ui/react/tabs";
import * as React from "react";
import {
  type SegmentedControlSize,
  segmentedControlItemLayoutClassName,
  segmentedControlItemSizeClassNames,
} from "../lib/segmented-control";
import { cn } from "../lib/utils";

type TabsVariant = "default" | "underline";
type TabsSize = SegmentedControlSize;

const TabsListContext: React.Context<{
  size: TabsSize;
  variant: TabsVariant;
}> = React.createContext<{ size: TabsSize; variant: TabsVariant }>({
  size: "default",
  variant: "default",
});

export function Tabs({
  className,
  ...props
}: TabsPrimitive.Root.Props): React.ReactElement {
  return (
    <TabsPrimitive.Root
      className={cn(
        "flex flex-col gap-2 data-[orientation=vertical]:flex-row",
        className,
      )}
      data-slot="tabs"
      {...props}
    />
  );
}

export function TabsList({
  variant = "default",
  size = "default",
  className,
  children,
  ...props
}: TabsPrimitive.List.Props & {
  size?: TabsSize;
  variant?: TabsVariant;
}): React.ReactElement {
  const context = React.useMemo(() => ({ size, variant }), [size, variant]);
  return (
    <TabsPrimitive.List
      className={cn(
        "relative z-0 flex w-fit items-center justify-center text-muted-foreground",
        "data-[orientation=vertical]:flex-col",
        variant === "default"
          ? "gap-x-0.5 rounded-lg bg-muted p-0.5 text-muted-foreground"
          : "gap-x-4 data-[orientation=vertical]:items-start data-[orientation=vertical]:gap-y-1",
        className,
      )}
      data-size={size}
      data-slot="tabs-list"
      data-variant={variant}
      {...props}
    >
      <TabsListContext.Provider value={context}>
        {children}
      </TabsListContext.Provider>
      <TabsPrimitive.Indicator
        className={cn(
          "absolute bottom-0 left-0 h-(--active-tab-height) w-(--active-tab-width) translate-x-(--active-tab-left) -translate-y-(--active-tab-bottom) transition-[width,translate] duration-200 ease-in-out",
          variant === "underline"
            ? "z-10 rounded-full bg-primary data-[orientation=horizontal]:h-0.5 data-[orientation=vertical]:w-0.5 data-[orientation=vertical]:-translate-x-px"
            : "-z-1 rounded-md bg-background shadow-sm/5 dark:bg-input",
        )}
        data-slot="tab-indicator"
      />
    </TabsPrimitive.List>
  );
}

export function TabsTab({
  className,
  size,
  ...props
}: TabsPrimitive.Tab.Props & {
  size?: TabsSize;
}): React.ReactElement {
  const context = React.useContext(TabsListContext);
  const resolvedSize: TabsSize = size ?? context.size;
  const underline = context.variant === "underline";

  return (
    <TabsPrimitive.Tab
      className={cn(
        "relative flex shrink-0 grow cursor-pointer items-center justify-center whitespace-nowrap border border-transparent font-medium outline-none transition-[color,background-color,box-shadow] focus-visible:ring-1 focus-visible:ring-ring/80 data-disabled:pointer-events-none data-[orientation=vertical]:w-full data-[orientation=vertical]:justify-start data-active:text-foreground data-disabled:opacity-64",
        segmentedControlItemLayoutClassName,
        underline
          ? "h-9 rounded-none px-0 text-[13.5px] hover:text-foreground"
          : cn(
              "rounded-md text-base hover:text-muted-foreground sm:text-sm",
              segmentedControlItemSizeClassNames[resolvedSize],
            ),
        className,
      )}
      data-size={resolvedSize}
      data-slot="tabs-tab"
      {...props}
    />
  );
}

export function TabsPanel({
  className,
  ...props
}: TabsPrimitive.Panel.Props): React.ReactElement {
  return (
    <TabsPrimitive.Panel
      className={cn("flex-1 outline-none", className)}
      data-slot="tabs-content"
      {...props}
    />
  );
}

export {
  TabsPrimitive,
  TabsTab as TabsTrigger,
  TabsPanel as TabsContent,
  type TabsSize,
  type TabsVariant,
};

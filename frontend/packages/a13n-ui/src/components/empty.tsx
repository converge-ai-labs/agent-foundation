import { cva, type VariantProps } from "class-variance-authority";
import type React from "react";
import { cn } from "../lib/utils";

const emptyMediaVariants = cva(
  "flex shrink-0 items-center justify-center [&_svg]:pointer-events-none [&_svg]:shrink-0",
  {
    defaultVariants: {
      variant: "default",
    },
    variants: {
      variant: {
        default: "bg-transparent",
        icon: "relative flex size-10 shrink-0 items-center justify-center rounded-[10px] bg-card text-muted-foreground shadow-[0_1px_2px_--theme(--color-black/6%)] [&_svg:not([class*='size-'])]:size-5",
      },
    },
  },
);

export function Empty({
  className,
  ...props
}: React.ComponentProps<"div">): React.ReactElement {
  return (
    <div
      className={cn(
        "flex min-w-0 flex-1 flex-col items-center justify-center gap-5 rounded-[12px] bg-muted px-6 py-12 text-balance text-center md:py-16",
        className,
      )}
      data-slot="empty"
      {...props}
    />
  );
}

export function EmptyHeader({
  className,
  ...props
}: React.ComponentProps<"div">): React.ReactElement {
  return (
    <div
      className={cn(
        "flex max-w-sm flex-col items-center text-center",
        className,
      )}
      data-slot="empty-header"
      {...props}
    />
  );
}

export function EmptyMedia({
  className,
  variant = "default",
  ...props
}: React.ComponentProps<"div"> &
  VariantProps<typeof emptyMediaVariants>): React.ReactElement {
  return (
    <div
      className={cn("relative mb-4", className)}
      data-slot="empty-media"
      data-variant={variant}
      {...props}
    >
      <div
        className={cn(emptyMediaVariants({ className, variant }))}
        {...props}
      />
    </div>
  );
}

export function EmptyTitle({
  className,
  ...props
}: React.ComponentProps<"div">): React.ReactElement {
  return (
    <div
      className={cn("font-heading font-semibold text-[15px]", className)}
      data-slot="empty-title"
      {...props}
    />
  );
}

export function EmptyDescription({
  className,
  ...props
}: React.ComponentProps<"p">): React.ReactElement {
  return (
    <div
      className={cn(
        "text-[13px] text-muted-foreground leading-relaxed [&>a:hover]:text-primary [&>a]:underline [&>a]:underline-offset-4 [[data-slot=empty-title]+&]:mt-1.5",
        className,
      )}
      data-slot="empty-description"
      {...props}
    />
  );
}

export function EmptyContent({
  className,
  ...props
}: React.ComponentProps<"div">): React.ReactElement {
  return (
    <div
      className={cn(
        "flex w-full min-w-0 max-w-sm flex-col items-center gap-3 text-balance text-[13px]",
        className,
      )}
      data-slot="empty-content"
      {...props}
    />
  );
}

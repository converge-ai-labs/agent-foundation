"use client";

import { Toast as ToastPrimitive } from "@base-ui/react/toast";
import {
  CheckCircleIcon,
  InfoIcon,
  WarningCircleIcon,
  WarningIcon,
  XIcon,
} from "@phosphor-icons/react";
import type React from "react";
import { cn } from "../lib/utils";

export const useToast = ToastPrimitive.useToastManager;

export function ToastProvider({
  children,
  closeLabel,
  viewportLabel = "Notifications",
}: {
  children: React.ReactNode;
  closeLabel: string;
  viewportLabel?: string;
}): React.ReactElement {
  return (
    <ToastPrimitive.Provider limit={3} timeout={6000}>
      {children}
      <ToastViewport closeLabel={closeLabel} viewportLabel={viewportLabel} />
    </ToastPrimitive.Provider>
  );
}

// Inline formulas compose swipe, stacking and enter motion; state classes only
// change their inputs, so no transform utility has to win a cascade.
const toastMotion = {
  translate:
    "var(--toast-swipe-movement-x, 0px) calc(var(--toast-swipe-movement-y, 0px) + var(--toast-stack-y, 0px) + var(--toast-enter-y, 0px))",
  scale: "calc(var(--toast-stack-scale, 1) * var(--toast-enter-scale, 1))",
} satisfies React.CSSProperties;

function ToastViewport({
  closeLabel,
  viewportLabel,
}: {
  closeLabel: string;
  viewportLabel: string;
}) {
  const { toasts } = ToastPrimitive.useToastManager();
  return (
    <ToastPrimitive.Portal>
      <ToastPrimitive.Viewport
        aria-label={viewportLabel}
        className="fixed top-[max(0.5rem,env(safe-area-inset-top))] left-1/2 z-100 flex w-[min(30rem,calc(100vw-1rem))] -translate-x-1/2 flex-col gap-1.5 outline-none sm:top-5 sm:w-[min(30rem,calc(100vw-2rem))] sm:gap-2"
      >
        {toasts.map((toast) => (
          <ToastPrimitive.Root
            key={toast.id}
            toast={toast}
            swipeDirection={["up", "left", "right"]}
            style={toastMotion}
            className={cn(
              "grid grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-x-2 overflow-hidden rounded-xl border bg-popover p-2 sm:gap-x-2.5 sm:p-3 text-popover-foreground shadow-lg/15 transition-[opacity,translate,scale,height] duration-200 data-swiping:duration-0 data-limited:hidden data-ending-style:opacity-0 data-starting-style:opacity-0 data-ending-style:[--toast-enter-y:-0.5rem] data-starting-style:[--toast-enter-y:-0.5rem] data-ending-style:[--toast-enter-scale:0.98] data-starting-style:[--toast-enter-scale:0.98]",
              // Phones keep one readable toast in front; older ones peek
              // behind it and fan out while the stack is expanded.
              "max-sm:absolute max-sm:inset-x-0 max-sm:top-0 max-sm:z-[calc(10-var(--toast-index))] max-sm:h-(--toast-frontmost-height) max-sm:origin-bottom max-sm:[--toast-stack-scale:calc(1-var(--toast-index)*0.05)] max-sm:[--toast-stack-y:calc(var(--toast-index)*0.5rem)] max-sm:not-data-expanded:*:opacity-[calc(1-var(--toast-index))] max-sm:*:transition-opacity max-sm:data-expanded:h-(--toast-height) max-sm:data-expanded:[--toast-stack-scale:1] max-sm:data-expanded:[--toast-stack-y:calc(var(--toast-offset-y)+var(--toast-index)*0.375rem)]",
            )}
          >
            <ToastStatusIcon type={toast.type} />
            <ToastPrimitive.Content className="min-w-0 self-center">
              <ToastPrimitive.Title className="max-sm:line-clamp-1 break-words text-[13px] leading-5 font-medium" />
              <ToastPrimitive.Description className="max-sm:line-clamp-2 break-words text-xs leading-[18px] text-muted-foreground [&_small]:mt-0.5 [&_small]:block [&_small]:break-all [&_small]:text-[10px] [&_small]:leading-4" />
            </ToastPrimitive.Content>
            <ToastPrimitive.Close
              aria-label={closeLabel}
              className="relative -me-1 -mt-1 grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-2 pointer-coarse:after:absolute pointer-coarse:after:-inset-2"
            >
              <XIcon aria-hidden="true" className="size-4" />
            </ToastPrimitive.Close>
            {toast.actionProps && (
              <ToastPrimitive.Action className="relative col-start-2 mt-1 w-fit rounded-md border px-2 py-0.5 text-xs leading-5 font-medium sm:mt-2 sm:px-2.5 sm:py-1 hover:bg-muted focus-visible:outline-2 pointer-coarse:after:absolute pointer-coarse:after:-inset-x-1 pointer-coarse:after:-inset-y-2.5" />
            )}
          </ToastPrimitive.Root>
        ))}
      </ToastPrimitive.Viewport>
    </ToastPrimitive.Portal>
  );
}

function ToastStatusIcon({ type }: { type?: string }) {
  const Icon =
    type === "error"
      ? WarningCircleIcon
      : type === "success"
        ? CheckCircleIcon
        : type === "warning"
          ? WarningIcon
          : InfoIcon;
  return (
    <span
      aria-hidden="true"
      className={cn(
        "grid size-6 sm:size-7.5 place-items-center rounded-full bg-info/10 text-info-foreground",
        type === "error" && "bg-destructive/10 text-destructive-foreground",
        type === "success" && "bg-success/10 text-success-foreground",
        type === "warning" && "bg-warning/10 text-warning-foreground",
      )}
    >
      <Icon className="size-3.5" weight="fill" />
    </span>
  );
}

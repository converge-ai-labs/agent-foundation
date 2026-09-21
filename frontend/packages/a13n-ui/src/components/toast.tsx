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
}: {
  children: React.ReactNode;
  closeLabel: string;
}): React.ReactElement {
  return (
    <ToastPrimitive.Provider limit={3} timeout={6000}>
      {children}
      <ToastViewport closeLabel={closeLabel} />
    </ToastPrimitive.Provider>
  );
}

function ToastViewport({ closeLabel }: { closeLabel: string }) {
  const { toasts } = ToastPrimitive.useToastManager();
  return (
    <ToastPrimitive.Portal>
      <ToastPrimitive.Viewport className="fixed top-[max(0.5rem,env(safe-area-inset-top))] left-1/2 z-100 flex w-[min(30rem,calc(100vw-1rem))] -translate-x-1/2 flex-col gap-1.5 outline-none sm:top-5 sm:w-[min(30rem,calc(100vw-2rem))] sm:gap-2">
        {toasts.map((toast) => (
          <ToastPrimitive.Root
            key={toast.id}
            toast={toast}
            className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-x-2 overflow-hidden rounded-xl border bg-popover p-2 sm:gap-x-2.5 sm:p-3 text-popover-foreground shadow-lg/15 transition-[opacity,translate,scale] duration-200 data-ending-style:-translate-y-2 data-starting-style:-translate-y-2 data-ending-style:scale-98 data-starting-style:scale-98 data-ending-style:opacity-0 data-starting-style:opacity-0"
          >
            <ToastStatusIcon type={toast.type} />
            <ToastPrimitive.Content className="min-w-0 self-center">
              <ToastPrimitive.Title className="max-sm:line-clamp-1 break-words text-[13px] leading-5 font-medium" />
              <ToastPrimitive.Description className="max-sm:line-clamp-2 break-words text-xs leading-[18px] text-muted-foreground [&_small]:mt-0.5 [&_small]:block [&_small]:break-all [&_small]:text-[10px] [&_small]:leading-4" />
            </ToastPrimitive.Content>
            <ToastPrimitive.Close
              aria-label={closeLabel}
              className="-me-1 -mt-1 grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-2"
            >
              <XIcon aria-hidden="true" className="size-4" />
            </ToastPrimitive.Close>
            {toast.actionProps && (
              <ToastPrimitive.Action className="col-start-2 mt-1 w-fit rounded-md border px-2 py-0.5 text-xs leading-5 font-medium sm:mt-2 sm:px-2.5 sm:py-1 hover:bg-muted focus-visible:outline-2" />
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

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
      <ToastPrimitive.Viewport className="fixed top-4 left-1/2 z-100 flex w-[min(30rem,calc(100vw-2rem))] -translate-x-1/2 flex-col gap-2 outline-none sm:top-5">
        {toasts.map((toast) => (
          <ToastPrimitive.Root
            key={toast.id}
            toast={toast}
            className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-start gap-x-2.5 overflow-hidden rounded-xl border bg-popover p-3 text-popover-foreground shadow-lg/15 transition-[opacity,translate,scale] duration-200 data-ending-style:-translate-y-2 data-starting-style:-translate-y-2 data-ending-style:scale-98 data-starting-style:scale-98 data-ending-style:opacity-0 data-starting-style:opacity-0"
          >
            <ToastStatusIcon type={toast.type} />
            <ToastPrimitive.Content className="min-w-0 self-center">
              <ToastPrimitive.Title className="text-[13px] leading-5 font-medium" />
              <ToastPrimitive.Description className="text-xs leading-[18px] text-muted-foreground [&_small]:mt-0.5 [&_small]:block [&_small]:break-all [&_small]:text-[10px] [&_small]:leading-4" />
            </ToastPrimitive.Content>
            <ToastPrimitive.Close
              aria-label={closeLabel}
              className="-me-1 -mt-1 grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-2"
            >
              <XIcon aria-hidden="true" className="size-4" />
            </ToastPrimitive.Close>
            {toast.actionProps && (
              <ToastPrimitive.Action className="col-start-2 mt-2 w-fit rounded-md border px-2.5 py-1 text-xs leading-5 font-medium hover:bg-muted focus-visible:outline-2" />
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
        "grid size-7.5 place-items-center rounded-full bg-info/10 text-info-foreground",
        type === "error" && "bg-destructive/10 text-destructive-foreground",
        type === "success" && "bg-success/10 text-success-foreground",
        type === "warning" && "bg-warning/10 text-warning-foreground",
      )}
    >
      <Icon className="size-3.5" weight="fill" />
    </span>
  );
}

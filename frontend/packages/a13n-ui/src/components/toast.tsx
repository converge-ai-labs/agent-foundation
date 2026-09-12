"use client";

import { Toast as ToastPrimitive } from "@base-ui/react/toast";
import { XIcon } from "@phosphor-icons/react";
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
      <ToastPrimitive.Viewport className="fixed end-4 top-4 z-100 flex w-[min(26rem,calc(100vw-2rem))] flex-col gap-2 outline-none sm:end-6 sm:top-6">
        {toasts.map((toast) => (
          <ToastPrimitive.Root
            key={toast.id}
            toast={toast}
            className={cn(
              "grid grid-cols-[0.25rem_minmax(0,1fr)_auto] gap-x-3 overflow-hidden rounded-xl border bg-popover p-3.5 text-popover-foreground shadow-xl/10 transition-[opacity,translate,scale] duration-200 data-ending-style:translate-x-2 data-starting-style:translate-x-2 data-ending-style:scale-98 data-starting-style:scale-98 data-ending-style:opacity-0 data-starting-style:opacity-0",
              toast.type === "error" && "border-destructive/24",
            )}
          >
            <span
              aria-hidden="true"
              className={cn(
                "row-span-2 h-full min-h-8 rounded-full bg-info",
                toast.type === "error" && "bg-destructive",
                toast.type === "success" && "bg-success",
                toast.type === "warning" && "bg-warning",
              )}
            />
            <ToastPrimitive.Content className="min-w-0 self-center">
              <ToastPrimitive.Title className="text-sm font-medium" />
              <ToastPrimitive.Description className="mt-0.5 text-sm text-muted-foreground [&_small]:mt-1.5 [&_small]:block [&_small]:text-xs" />
            </ToastPrimitive.Content>
            <ToastPrimitive.Close
              aria-label={closeLabel}
              className="row-start-1 -me-1 -mt-1 grid size-7 place-items-center rounded-md text-muted-foreground hover:bg-muted hover:text-foreground focus-visible:outline-2"
            >
              <XIcon aria-hidden="true" className="size-4" />
            </ToastPrimitive.Close>
            {toast.actionProps && (
              <ToastPrimitive.Action className="col-start-2 mt-2.5 w-fit rounded-md border px-2.5 py-1.5 text-xs font-medium hover:bg-muted focus-visible:outline-2" />
            )}
          </ToastPrimitive.Root>
        ))}
      </ToastPrimitive.Viewport>
    </ToastPrimitive.Portal>
  );
}

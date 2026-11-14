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
import { useEffect } from "react";
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

function ToastViewport({
  closeLabel,
  viewportLabel,
}: {
  closeLabel: string;
  viewportLabel: string;
}) {
  const { toasts, close } = ToastPrimitive.useToastManager();
  const frontId = toasts.find(
    (toast) => toast.transitionStatus !== "ending",
  )?.id;
  // A burst replaces the back of the deck instead of creating an unbounded
  // hidden queue (especially for notices with timeout: 0). Receipt identity
  // and durable recovery remain the application's responsibility.
  useEffect(() => {
    for (const toast of toasts) {
      if (toast.limited && toast.transitionStatus !== "ending") close(toast.id);
    }
  }, [toasts, close]);
  return (
    <ToastPrimitive.Portal>
      <ToastPrimitive.Viewport
        aria-label={viewportLabel}
        className="a13n-toast-viewport"
      >
        {toasts.map((toast) => (
          <ToastPrimitive.Root
            key={toast.id}
            toast={toast}
            data-front={toast.id === frontId ? "" : undefined}
            className="a13n-toast"
            swipeDirection={["left", "right", "up"]}
          >
            <ToastStatusIcon type={toast.type} />
            <ToastPrimitive.Content className="a13n-toast-content">
              <ToastPrimitive.Title className="a13n-toast-title" />
              <ToastPrimitive.Description className="a13n-toast-description" />
              {toast.actionProps && (
                <ToastPrimitive.Action className="a13n-toast-action" />
              )}
            </ToastPrimitive.Content>
            <ToastPrimitive.Close
              aria-label={closeLabel}
              className="a13n-toast-close"
            >
              <XIcon aria-hidden="true" className="size-4" />
            </ToastPrimitive.Close>
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
        "a13n-toast-icon grid size-6 place-items-center rounded-full bg-info/10 text-info-foreground",
        type === "error" && "bg-destructive/10 text-destructive-foreground",
        type === "success" && "bg-success/10 text-success-foreground",
        type === "warning" && "bg-warning/10 text-warning-foreground",
      )}
    >
      <Icon className="size-3.5" weight="fill" />
    </span>
  );
}

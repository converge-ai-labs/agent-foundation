import type { ComponentProps, ReactElement, ReactNode } from "react";
import {
  Dialog,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogPanel,
  DialogPopup,
  DialogTitle,
  DialogTrigger,
} from "../components/dialog";
import { cn } from "../lib/utils";

/** Resource forms share a title, a bounded scroll region, and action placement. */
export function ModalFrame({
  trigger,
  title,
  description,
  closeLabel,
  size = "md",
  children,
  footer,
  ...props
}: Omit<ComponentProps<typeof Dialog>, "children"> & {
  trigger: ReactElement;
  title: ReactNode;
  description?: ReactNode;
  closeLabel: string;
  size?: "md" | "lg";
  children?: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <Dialog {...props}>
      <DialogTrigger render={trigger} />
      <DialogPopup
        className={cn("max-sm:rounded-t-2xl", size === "lg" && "sm:max-w-3xl")}
        closeProps={{ "aria-label": closeLabel }}
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        <DialogPanel
          scrollFade={false}
          className="[&_[data-a13n-form-actions]]:sticky [&_[data-a13n-form-actions]]:bottom-0 [&_[data-a13n-form-actions]]:bg-popover"
        >
          {children}
        </DialogPanel>
        {footer && <DialogFooter variant="bare">{footer}</DialogFooter>}
      </DialogPopup>
    </Dialog>
  );
}

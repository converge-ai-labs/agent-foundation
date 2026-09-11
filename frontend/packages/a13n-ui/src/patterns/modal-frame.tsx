import {
  memo,
  useState,
  type ComponentProps,
  type ReactElement,
  type ReactNode,
} from "react";
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
  finalFocus,
  size = "md",
  children,
  footer,
  open: controlledOpen,
  defaultOpen = false,
  onOpenChange,
  ...props
}: Omit<ComponentProps<typeof Dialog>, "children"> & {
  trigger?: ReactElement;
  finalFocus?: ComponentProps<typeof DialogPopup>["finalFocus"];
  title: ReactNode;
  description?: ReactNode;
  closeLabel: string;
  size?: "md" | "lg";
  children?: ReactNode;
  footer?: ReactNode;
}) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(defaultOpen);
  const open = controlledOpen ?? uncontrolledOpen;
  return (
    <Dialog
      {...props}
      open={open}
      onOpenChange={(nextOpen, details) => {
        onOpenChange?.(nextOpen, details);
        if (!details.isCanceled) setUncontrolledOpen(nextOpen);
      }}
    >
      {trigger && <DialogTrigger render={trigger} />}
      <ModalSurface
        open={open}
        title={title}
        description={description}
        closeLabel={closeLabel}
        finalFocus={finalFocus}
        size={size}
        footer={footer}
      >
        {children}
      </ModalSurface>
    </Dialog>
  );
}

// Keep the last visible content until the exit transition unmounts the portal.
// Callers may clear their form or conditional children as soon as open becomes false.
const ModalSurface = memo(
  function ModalSurface({
    title,
    description,
    closeLabel,
    finalFocus,
    size,
    children,
    footer,
  }: {
    open: boolean;
    title: ReactNode;
    description?: ReactNode;
    closeLabel: string;
    finalFocus?: ComponentProps<typeof DialogPopup>["finalFocus"];
    size: "md" | "lg";
    children?: ReactNode;
    footer?: ReactNode;
  }) {
    return (
      <DialogPopup
        className={cn(size === "lg" && "sm:max-w-[45rem]")}
        finalFocus={finalFocus}
        closeProps={{ "aria-label": closeLabel }}
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        {children != null && children !== false && (
          <DialogPanel
            scrollFade={false}
            className="has-[[data-a13n-form-actions]]:pb-0 [&_form]:gap-4 [&_[data-a13n-form-actions]]:pt-4 [&_[data-a13n-form-actions]]:pb-6 [&_[data-a13n-form-actions]]:z-10 [&_[data-a13n-form-actions]]:sticky [&_[data-a13n-form-actions]]:bottom-0 [&_[data-a13n-form-actions]]:bg-popover"
          >
            {children}
          </DialogPanel>
        )}
        {footer && <DialogFooter variant="bare">{footer}</DialogFooter>}
      </DialogPopup>
    );
  },
  (_previous, next) => !next.open,
);

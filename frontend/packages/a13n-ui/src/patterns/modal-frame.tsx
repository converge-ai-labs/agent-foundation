import {
  memo,
  useEffect,
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
import { useMenuActions } from "../components/menu";
import { cn } from "../lib/utils";

/** Resource forms share a title, a bounded scroll region, and action placement. */
export function ModalFrame({
  trigger,
  title,
  description,
  closeLabel,
  initialFocus,
  finalFocus,
  keepVisibleWhenNested = false,
  size = "md",
  children,
  footer,
  placement = "center",
  open: controlledOpen,
  defaultOpen = false,
  onOpenChange,
  ...props
}: Omit<ComponentProps<typeof Dialog>, "children"> & {
  trigger?: ReactElement;
  initialFocus?: ComponentProps<typeof DialogPopup>["initialFocus"];
  finalFocus?: ComponentProps<typeof DialogPopup>["finalFocus"];
  /** Keep the form visible underneath a nested confirmation or error dialog. */
  keepVisibleWhenNested?: boolean;
  title: ReactNode;
  description?: ReactNode;
  closeLabel: string;
  size?: "md" | "lg" | "xl";
  placement?: "center" | "top";
  children?: ReactNode;
  footer?: ReactNode;
}) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState(defaultOpen);
  const open = controlledOpen ?? uncontrolledOpen;
  // A menu that opened this dialog stays hidden until the dialog has left,
  // then closes, so it never shows through the backdrop or comes back.
  const menu = useMenuActions();
  const [present, setPresent] = useState(open);
  useEffect(() => {
    if (open) setPresent(true);
  }, [open]);
  return (
    <Dialog
      {...props}
      open={open}
      onOpenChange={(nextOpen, details) => {
        onOpenChange?.(nextOpen, details);
        if (!details.isCanceled) setUncontrolledOpen(nextOpen);
      }}
      onOpenChangeComplete={(nextOpen) => {
        props.onOpenChangeComplete?.(nextOpen);
        if (nextOpen) return;
        setPresent(false);
        menu?.current?.close();
      }}
    >
      {trigger && (
        <DialogTrigger
          render={trigger}
          data-a13n-dialog-open={open || present ? "" : undefined}
        />
      )}
      <ModalSurface
        open={open}
        title={title}
        description={description}
        closeLabel={closeLabel}
        initialFocus={initialFocus}
        finalFocus={finalFocus}
        keepVisibleWhenNested={keepVisibleWhenNested}
        size={size}
        placement={placement}
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
    initialFocus,
    finalFocus,
    keepVisibleWhenNested,
    size,
    children,
    footer,
    placement,
  }: {
    open: boolean;
    title: ReactNode;
    description?: ReactNode;
    closeLabel: string;
    initialFocus?: ComponentProps<typeof DialogPopup>["initialFocus"];
    finalFocus?: ComponentProps<typeof DialogPopup>["finalFocus"];
    keepVisibleWhenNested: boolean;
    size: "md" | "lg" | "xl";
    placement: "center" | "top";
    children?: ReactNode;
    footer?: ReactNode;
  }) {
    return (
      <DialogPopup
        data-a13n-modal
        // Keep typing out of the hidden menu, but let Escape reach dialog dismissal.
        onKeyDown={(event) => {
          if (event.key !== "Escape") event.stopPropagation();
        }}
        data-size={size}
        className={cn(
          keepVisibleWhenNested && "opacity-100",
          size === "lg" && "sm:max-w-[40rem]",
          size === "xl" && "sm:max-w-[55rem]",
        )}
        initialFocus={initialFocus}
        finalFocus={finalFocus}
        placement={placement}
        closeProps={{ "aria-label": closeLabel }}
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          {description && <DialogDescription>{description}</DialogDescription>}
        </DialogHeader>
        {children != null && children !== false && (
          <DialogPanel
            scrollFade={false}
            className="has-[[data-a13n-form-actions]:not([hidden]_*)]:pb-0 [&_form]:gap-4 [&_[data-a13n-form-actions]]:pt-4 [&_[data-a13n-form-actions]]:pb-6 [&_[data-a13n-form-actions]]:z-10 [&_[data-a13n-form-actions]]:sticky [&_[data-a13n-form-actions]]:bottom-0 [&_[data-a13n-form-actions]]:bg-popover"
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

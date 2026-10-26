import type { ModalFrame } from "a13n-ui";
import { useState, type ComponentProps } from "react";

/** Let a conditionally mounted editor finish its exit before its owner removes it. */
export function useMountedModal(onClosed: () => void) {
  const [open, setOpen] = useState(true);
  return {
    close: () => setOpen(false),
    modalProps: {
      open,
      onOpenChange: setOpen,
      onOpenChangeComplete: (open) => {
        if (!open) onClosed();
      },
    } satisfies Pick<
      ComponentProps<typeof ModalFrame>,
      "open" | "onOpenChange" | "onOpenChangeComplete"
    >,
  };
}

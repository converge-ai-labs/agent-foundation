import { useRef, useState } from "react";

export type ResourceEditorControl = {
  controlledOpen?: boolean;
  onClose?: () => void;
  finalFocus?: React.RefObject<HTMLElement | null>;
};

export function useResourceRows<T>() {
  const [selected, setSelected] = useState<T>();
  const [open, setOpen] = useState(false);
  const finalFocus = useRef<HTMLElement | null>(null);
  return {
    selected,
    control: {
      controlledOpen: open,
      onClose: () => setOpen(false),
      finalFocus,
    },
    activate: (item: T, element: HTMLElement) => {
      finalFocus.current = element;
      setSelected(item);
      setOpen(true);
    },
  };
}

export function useResourceEditorState({
  controlledOpen,
  onClose,
  finalFocus,
}: ResourceEditorControl) {
  const [localOpen, setLocalOpen] = useState(false);
  const open = controlledOpen ?? localOpen;
  const setOpen = (value: boolean) => {
    setLocalOpen(value);
    if (!value) onClose?.();
  };
  return {
    open,
    setOpen,
    modalProps: { open, finalFocus, onOpenChange: setOpen },
  };
}

import { createContext, useContext, type ReactNode } from "react";
import { createPortal } from "react-dom";

// The conversation owns live work data; the workspace owns its presentation slot.
export const DetailsOutlet = createContext<{
  element: HTMLElement | null;
  visible: boolean;
}>({ element: null, visible: false });

export function ConversationDetailsPortal({
  children,
}: {
  children: ReactNode;
}) {
  const { element, visible } = useContext(DetailsOutlet);
  return element && visible ? createPortal(children, element) : null;
}

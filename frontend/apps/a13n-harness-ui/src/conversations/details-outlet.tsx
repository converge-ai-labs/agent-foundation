import {
  createContext,
  useContext,
  useState,
  type ReactNode,
  type Ref,
} from "react";
import {
  Button,
  Popover,
  PopoverTrigger,
  PopoverPopup,
  PopoverTitle,
  PopoverClose,
} from "a13n-ui";
import { ListBullets, PencilSimple, X } from "@phosphor-icons/react";
import { useTranslation } from "../i18n";
import styles from "./details-outlet.module.css";
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

export function ChatDetailsPopover({
  title,
  outletRef,
  onRename,
}: {
  title?: string;
  outletRef: Ref<HTMLDivElement>;
  onRename?: () => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={<Button variant="ghost" size="sm" />}
        className={styles.trigger}
      >
        <ListBullets />
        {t("Chat details")}
      </PopoverTrigger>
      <PopoverPopup align="end" sideOffset={12} className={styles.popup}>
        <div className={styles.header}>
          <PopoverTitle>{t("Chat details")}</PopoverTitle>
          <PopoverClose
            render={<Button variant="ghost" size="icon-xs" />}
            aria-label={t("Close")}
          >
            <X />
          </PopoverClose>
        </div>
        {title && (
          <div className={styles.title}>
            <p>{title}</p>
            {onRename && (
              <Button
                variant="ghost"
                size="icon-xs"
                aria-label={t("Rename")}
                onClick={() => {
                  setOpen(false);
                  onRename();
                }}
              >
                <PencilSimple />
              </Button>
            )}
          </div>
        )}
        {open && <div ref={outletRef} />}
      </PopoverPopup>
    </Popover>
  );
}

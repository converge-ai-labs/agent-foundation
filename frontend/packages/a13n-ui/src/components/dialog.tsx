import type { ReactElement, ReactNode } from "react";
import * as Primitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { Button } from "./button";
import styles from "./overlay.module.css";
export interface DialogProps {
  trigger: ReactElement;
  title: string;
  description: string;
  closeLabel: string;
  children?: ReactNode;
  footer?: ReactNode;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}
export function Dialog({
  trigger,
  title,
  description,
  closeLabel,
  children,
  footer,
  open,
  onOpenChange,
}: DialogProps) {
  return (
    <Primitive.Root open={open} onOpenChange={onOpenChange}>
      <Primitive.Trigger asChild>{trigger}</Primitive.Trigger>
      <Primitive.Portal>
        <Primitive.Overlay className={styles.backdrop} />
        <Primitive.Content className={styles.dialog}>
          <div className={styles.heading}>
            <Primitive.Title className={styles.title}>{title}</Primitive.Title>
            <Primitive.Close asChild>
              <Button
                variant="ghost"
                aria-label={closeLabel}
                icon={<X size={16} />}
              />
            </Primitive.Close>
          </div>
          <Primitive.Description className={styles.description}>
            {description}
          </Primitive.Description>
          {children}
          {footer && <div className={styles.footer}>{footer}</div>}
        </Primitive.Content>
      </Primitive.Portal>
    </Primitive.Root>
  );
}

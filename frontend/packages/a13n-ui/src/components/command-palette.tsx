import type { ReactElement } from "react";
import * as Primitive from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { Button } from "./button";
import { SearchList, type SearchListProps } from "./search-list";
import styles from "./command-palette.module.css";
import overlay from "./overlay.module.css";
export interface CommandPaletteProps extends Omit<
  SearchListProps,
  "selectedValue"
> {
  trigger: ReactElement;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  closeLabel: string;
}
export function CommandPalette({
  trigger,
  open,
  onOpenChange,
  closeLabel,
  ...list
}: CommandPaletteProps) {
  return (
    <Primitive.Root open={open} onOpenChange={onOpenChange}>
      <Primitive.Trigger asChild>{trigger}</Primitive.Trigger>
      <Primitive.Portal>
        <Primitive.Overlay className={overlay.backdrop} />
        <Primitive.Content
          className={styles.content}
          aria-describedby={undefined}
        >
          <Primitive.Title className={styles.title}>
            {list.label}
          </Primitive.Title>
          <SearchList
            {...list}
            onSelect={(value) => {
              onOpenChange(false);
              list.onSelect(value);
            }}
          />
          <Primitive.Close asChild>
            <Button
              className={styles.close}
              size="sm"
              variant="ghost"
              icon={<X size={14} />}
              aria-label={closeLabel}
            />
          </Primitive.Close>
        </Primitive.Content>
      </Primitive.Portal>
    </Primitive.Root>
  );
}

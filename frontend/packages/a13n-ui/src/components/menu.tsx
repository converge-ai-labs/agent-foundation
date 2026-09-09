import type { ReactElement, ReactNode } from "react";
import * as Primitive from "@radix-ui/react-dropdown-menu";
import { OptionContent } from "./option-content";
import { Check, ChevronRight } from "lucide-react";
import { Kbd } from "./kbd";
import styles from "./menu.module.css";
interface MenuActionBase {
  id: string;
  label: string;
  description?: string;
  icon?: ReactNode;
  shortcut?: string;
  disabled?: boolean;
  danger?: boolean;
  selected?: boolean;
}
export type MenuAction = MenuActionBase &
  (
    | { onSelect: () => void; items?: never }
    | { items: readonly MenuAction[]; onSelect?: never }
  );
export interface MenuGroup {
  label?: string;
  actions: readonly MenuAction[];
}
export interface MenuProps {
  trigger: ReactElement;
  label: string;
  groups: readonly MenuGroup[];
  align?: "start" | "center" | "end";
}
export function Menu({ trigger, label, groups, align = "end" }: MenuProps) {
  return (
    <Primitive.Root>
      <Primitive.Trigger asChild>{trigger}</Primitive.Trigger>
      <Primitive.Portal>
        <Primitive.Content
          aria-label={label}
          align={align}
          sideOffset={5}
          collisionPadding={8}
          className={styles.surface}
        >
          {groups.map((group, index) => (
            <Primitive.Group key={index}>
              {index > 0 && (
                <Primitive.Separator className={styles.separator} />
              )}{" "}
              {group.label && (
                <Primitive.Label className={styles.label}>
                  {group.label}
                </Primitive.Label>
              )}
              {group.actions.map((action) => (
                <Action key={action.id} action={action} />
              ))}
            </Primitive.Group>
          ))}
        </Primitive.Content>
      </Primitive.Portal>
    </Primitive.Root>
  );
}

function Action({ action }: { action: MenuAction }) {
  if (action.items)
    return (
      <Primitive.Sub>
        <Primitive.SubTrigger
          disabled={action.disabled}
          textValue={action.label}
          className={styles.item}
        >
          <OptionContent {...action} trailing={<ChevronRight size={14} />} />
        </Primitive.SubTrigger>
        <Primitive.Portal>
          <Primitive.SubContent
            aria-label={action.label}
            sideOffset={5}
            collisionPadding={8}
            className={styles.surface}
          >
            {action.items.map((item) => (
              <Action key={item.id} action={item} />
            ))}
          </Primitive.SubContent>
        </Primitive.Portal>
      </Primitive.Sub>
    );
  return (
    <Primitive.Item
      disabled={action.disabled}
      textValue={action.label}
      onSelect={action.onSelect}
      aria-current={action.selected ? "true" : undefined}
      data-danger={action.danger}
      className={styles.item}
    >
      <OptionContent
        {...action}
        trailing={
          action.selected ? (
            <Check size={14} />
          ) : action.shortcut ? (
            <Kbd>{action.shortcut}</Kbd>
          ) : undefined
        }
      />
    </Primitive.Item>
  );
}

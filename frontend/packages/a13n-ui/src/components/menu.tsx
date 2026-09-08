import type { ReactElement, ReactNode } from "react";
import * as Primitive from "@radix-ui/react-dropdown-menu";
import { OptionContent } from "./option-content";
import { Kbd } from "./kbd";
import styles from "./menu.module.css";
export interface MenuAction {
  id: string;
  label: string;
  description?: string;
  icon?: ReactNode;
  shortcut?: string;
  disabled?: boolean;
  danger?: boolean;
  onSelect: () => void;
}
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
                <Primitive.Item
                  key={action.id}
                  disabled={action.disabled}
                  textValue={action.label}
                  onSelect={action.onSelect}
                  data-danger={action.danger}
                  className={styles.item}
                >
                  <OptionContent
                    {...action}
                    trailing={action.shortcut && <Kbd>{action.shortcut}</Kbd>}
                  />
                </Primitive.Item>
              ))}
            </Primitive.Group>
          ))}
        </Primitive.Content>
      </Primitive.Portal>
    </Primitive.Root>
  );
}

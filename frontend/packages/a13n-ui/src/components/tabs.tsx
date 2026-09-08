import type { ComponentProps, ReactNode } from "react";
import * as Primitive from "@radix-ui/react-tabs";
import styles from "./tabs.module.css";
export interface TabItem {
  value: string;
  label: string;
  content: ReactNode;
  disabled?: boolean;
}
export type TabsProps = Omit<
  ComponentProps<typeof Primitive.Root>,
  "children"
> & { label: string; items: readonly TabItem[] };
export function Tabs({ label, items, className = "", ...props }: TabsProps) {
  return (
    <Primitive.Root {...props} className={`${styles.root} ${className}`}>
      <Primitive.List aria-label={label} className={styles.list}>
        {items.map((item) => (
          <Primitive.Trigger
            key={item.value}
            value={item.value}
            disabled={item.disabled}
            className={styles.trigger}
          >
            {item.label}
          </Primitive.Trigger>
        ))}
      </Primitive.List>
      {items.map((item) => (
        <Primitive.Content
          key={item.value}
          value={item.value}
          className={styles.content}
        >
          {item.content}
        </Primitive.Content>
      ))}
    </Primitive.Root>
  );
}

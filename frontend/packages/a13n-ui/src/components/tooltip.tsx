import type { ReactElement } from "react";
import * as Primitive from "@radix-ui/react-tooltip";
import styles from "./overlay.module.css";
export interface TooltipProps {
  children: ReactElement;
  content: string;
}
export function Tooltip({ children, content }: TooltipProps) {
  return (
    <Primitive.Provider delayDuration={350}>
      <Primitive.Root>
        <Primitive.Trigger asChild>{children}</Primitive.Trigger>
        <Primitive.Portal>
          <Primitive.Content
            sideOffset={6}
            collisionPadding={8}
            className={styles.tooltip}
          >
            {content}
          </Primitive.Content>
        </Primitive.Portal>
      </Primitive.Root>
    </Primitive.Provider>
  );
}

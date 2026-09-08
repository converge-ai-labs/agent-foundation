import { useId } from "react";
import type { ComponentProps } from "react";
import * as Primitive from "@radix-ui/react-switch";
import styles from "./toggle.module.css";
export type SwitchProps = ComponentProps<typeof Primitive.Root> & {
  label: string;
  labelHidden?: boolean;
};
export function Switch({
  label,
  labelHidden = false,
  id: providedId,
  className = "",
  ...props
}: SwitchProps) {
  const generatedId = useId();
  const id = providedId ?? generatedId;
  return (
    <div className={styles.row}>
      <Primitive.Root
        {...props}
        id={id}
        aria-label={label}
        className={`${styles.switch} ${className}`}
      >
        <Primitive.Thumb className={styles.thumb} />
      </Primitive.Root>
      <label htmlFor={id} className={labelHidden ? styles.hidden : undefined}>
        {label}
      </label>
    </div>
  );
}

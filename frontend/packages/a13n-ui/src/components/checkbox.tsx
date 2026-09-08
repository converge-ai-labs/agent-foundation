import { useId } from "react";
import type { ComponentProps } from "react";
import * as Primitive from "@radix-ui/react-checkbox";
import { Check, Minus } from "lucide-react";
import styles from "./toggle.module.css";
export type CheckboxProps = ComponentProps<typeof Primitive.Root> & {
  label: string;
};
export function Checkbox({
  label,
  id: providedId,
  className = "",
  ...props
}: CheckboxProps) {
  const generatedId = useId();
  const id = providedId ?? generatedId;
  return (
    <div className={styles.row}>
      <Primitive.Root
        {...props}
        id={id}
        className={`${styles.checkbox} ${className}`}
      >
        <Primitive.Indicator className={styles.indicator}>
          <Check className={styles.check} size={12} />
          <Minus className={styles.minus} size={12} />
        </Primitive.Indicator>
      </Primitive.Root>
      <label htmlFor={id}>{label}</label>
    </div>
  );
}

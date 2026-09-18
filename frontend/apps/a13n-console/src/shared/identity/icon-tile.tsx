import type { ReactNode } from "react";
import styles from "./identity.module.css";

/** Surface tile that frames an icon, avatar, or brand mark at a fixed size. */
export function IconTile({
  size = 32,
  tone = "surface",
  className,
  children,
}: {
  size?: 32 | 36 | 44;
  tone?: "surface" | "elevated";
  className?: string;
  children: ReactNode;
}) {
  return (
    <span
      aria-hidden={undefined}
      className={`${styles.iconTile} ${className ?? ""}`}
      data-size={size}
      data-tone={tone}
    >
      {children}
    </span>
  );
}

import { Graph } from "@phosphor-icons/react";
import styles from "./coordinator.module.css";

export function CoordinatorIcon({ size = 20 }: { size?: number }) {
  return (
    <Graph
      size={size}
      weight="duotone"
      className={styles.icon}
      aria-hidden="true"
    />
  );
}

export function CoordinatorMark() {
  return (
    <span
      className={styles.mark}
      role="img"
      aria-label="Coordinator"
      title="Coordinator"
    >
      <CoordinatorIcon size={18} />
    </span>
  );
}

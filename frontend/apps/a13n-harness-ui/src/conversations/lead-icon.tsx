import { Graph } from "@phosphor-icons/react";
import styles from "./project-lead.module.css";

export function LeadIcon({ size = 20 }: { size?: number }) {
  return (
    <Graph
      size={size}
      weight="duotone"
      className={styles.icon}
      aria-hidden="true"
    />
  );
}

export function LeadMark() {
  return (
    <span
      className={styles.mark}
      role="img"
      aria-label="Coordinator"
      title="Coordinator"
    >
      <LeadIcon size={18} />
    </span>
  );
}

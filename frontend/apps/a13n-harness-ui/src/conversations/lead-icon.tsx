import { Compass } from "@phosphor-icons/react";
import styles from "./project-lead.module.css";

export function LeadIcon({ size = 20 }: { size?: number }) {
  return (
    <Compass
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
      aria-label="Project Lead"
      title="Project Lead"
    >
      <LeadIcon size={18} />
    </span>
  );
}

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

export function LeadBadge() {
  return (
    <span className={styles.badge}>
      <LeadIcon size={16} />
      Project Lead
    </span>
  );
}

import type { TimelineEdit } from "../timeline";
import styles from "./diff-badges.module.css";

/** The one number a reader wants on a collapsed edit row, at both levels. */
export function DiffBadges({ diff }: { diff: TimelineEdit["diff"] }) {
  if (!diff.added && !diff.removed) return null;
  return (
    <span className={styles.diffBadges}>
      {!!diff.added && <span data-sign="added">+{diff.added}</span>}
      {!!diff.removed && <span data-sign="removed">−{diff.removed}</span>}
    </span>
  );
}

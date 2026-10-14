import { ModalFrame } from "a13n-ui";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import { ThreadEnvironmentMounts } from "./environment-mounts";
import { ThreadMemoryMounts } from "./memory-mounts";
import styles from "./conversations.module.css";

/**
 * What this conversation mounts for its later Runs. A Run keeps the mounts it
 * started with; its Details dialog shows those.
 */
export function ThreadMountsDialog({
  thread,
  open,
  onOpenChange,
}: {
  thread: Schema["ThreadView"];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation();
  return (
    <ModalFrame
      open={open}
      onOpenChange={onOpenChange}
      size="lg"
      title={t("Environments and memory")}
      description={t(
        "Mounts apply to this conversation's later runs. A run keeps the mounts it started with.",
      )}
      closeLabel={t("Close")}
    >
      <div className={styles.threadMounts}>
        <ThreadEnvironmentMounts thread={thread} />
        <ThreadMemoryMounts thread={thread} />
      </div>
    </ModalFrame>
  );
}

import { Button } from "a13n-ui";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import styles from "./bots.module.css";

type Details = Pick<
  Schema["Document"],
  "owner_name" | "access_reasons" | "correction_of" | "shared"
>;

export function MemoryProvenance({
  document,
  onOpen,
}: {
  document: Details;
  onOpen: (id: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <section
      className={styles.provenance}
      aria-label={t("Memory source and access")}
    >
      {document.owner_name && (
        <p>
          {t("Owning group")}: {document.owner_name}
        </p>
      )}
      <ul>
        {(document.access_reasons ?? []).map((reason) => (
          <li key={reason.kind}>
            {reason.kind === "installation"
              ? t(
                  "The owning group makes its memory visible to all connected groups.",
                )
              : t("This memory belongs to this group.")}
          </li>
        ))}
      </ul>
      <p>
        {document.shared
          ? t(
              "Shared memory is read-only. Only the owning group can delete it.",
            )
          : t("Saved memory is immutable. Corrections create a new document.")}
      </p>
      <div className={styles.memoryActions}>
        {!document.shared && document.correction_of && (
          <Button
            variant="ghost"
            onClick={() => onOpen(document.correction_of!)}
          >
            {t("View corrected memory")}
          </Button>
        )}
      </div>
    </section>
  );
}

import { Button } from "a13n-ui";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import styles from "./bots.module.css";

type Details = Pick<
  Schema["Document"],
  | "owner_name"
  | "access_reasons"
  | "more_access_reasons"
  | "correction_of"
  | "publication_source_id"
  | "shared"
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
          <li key={reason.policy_id ?? reason.kind}>
            {reason.kind === "policy"
              ? `${t("Shared through policy")}: ${reason.policy_name}`
              : reason.kind === "publication"
                ? t("This group received an approved publication.")
                : t("This memory belongs to this group.")}
          </li>
        ))}
      </ul>
      {document.more_access_reasons && (
        <p>
          {t(
            "Additional policies grant access. Review Sharing settings for the full policy list.",
          )}
        </p>
      )}
      <p>
        {document.shared
          ? t(
              "Shared memory is read-only. Access does not grant permission to modify, delete, or share it.",
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
        {!document.shared && document.publication_source_id && (
          <Button
            variant="ghost"
            onClick={() => onOpen(document.publication_source_id!)}
          >
            {t("View publication source")}
          </Button>
        )}
      </div>
    </section>
  );
}

import { DisclosureSection } from "a13n-ui";
import { WarningCircleIcon } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { JsonView } from "../../../shared/forms";
import styles from "./transcript.module.css";

/**
 * A run that stopped short explains itself in plain language; the payload stays
 * behind a disclosure and the way forward sits in the same notice.
 */
export function FailureNotice({
  failure,
  cancelled = false,
  action,
}: {
  failure?: unknown;
  cancelled?: boolean;
  action?: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <section className={styles.failure}>
      <div className={styles.failureHeading}>
        <WarningCircleIcon size={14} aria-hidden="true" />
        <h3>
          {t(cancelled ? "The run was stopped" : "The run could not finish")}
        </h3>
        {action && <div className={styles.failureActions}>{action}</div>}
      </div>
      <p>{t("Your messages are saved. Review the error details.")}</p>
      {failure != null && (
        <DisclosureSection
          className={styles.inlineDisclosure}
          title={<>{t("Error details")}</>}
        >
          <JsonView value={failure} />
        </DisclosureSection>
      )}
    </section>
  );
}

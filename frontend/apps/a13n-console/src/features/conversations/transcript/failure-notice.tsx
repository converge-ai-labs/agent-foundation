import { DisclosureSection } from "a13n-ui";
import { StopCircleIcon, WarningCircleIcon } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { JsonView } from "../../../shared/forms";
import styles from "./transcript.module.css";

/**
 * A run that stopped short explains itself in plain language; the payload stays
 * behind a disclosure and the way forward sits in the same notice. Only a
 * failure is an alarm: a run someone stopped is a neutral fact.
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
  const Glyph = cancelled ? StopCircleIcon : WarningCircleIcon;
  return (
    <section
      className={styles.failure}
      data-tone={cancelled ? "neutral" : "danger"}
    >
      <div className={styles.failureHeading}>
        <Glyph size={15} aria-hidden="true" />
        <h3>
          {t(cancelled ? "The run was stopped" : "The run could not finish")}
        </h3>
        {action && <div className={styles.failureActions}>{action}</div>}
      </div>
      <p>
        {t(
          failure != null
            ? "Your messages are saved. Review the error details."
            : "Your messages are saved.",
        )}
      </p>
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

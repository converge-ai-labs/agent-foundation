import { DisclosureSection } from "a13n-ui";

import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import { JsonView } from "../../shared/forms";
import styles from "./conversations.module.css";

export function PendingFeedback({
  actions,
}: {
  actions: Schema["PendingActionResource"][];
}) {
  const { t } = useTranslation();
  return (
    <section className={styles.pending}>
      <h3>{t("Waiting for a response")}</h3>
      <p>
        {t(
          "The agent paused this run for input or approval. It resumes when the host application responds.",
        )}
      </p>
      {actions.map((action) => (
        <div key={action.call_id} className={styles.pendingAction}>
          <strong>{action.tool_name ?? action.call_id}</strong>
          <small>{t(action.kind)}</small>
          {action.presentation != null && (
            <DisclosureSection title={<>{t("Request details")}</>}>
              <JsonView value={action.presentation} />
            </DisclosureSection>
          )}
        </div>
      ))}
    </section>
  );
}

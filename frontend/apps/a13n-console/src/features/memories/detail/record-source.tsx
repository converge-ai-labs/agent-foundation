import { DisclosureSection, ModalFrame } from "a13n-ui";
import { Fragment } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../../shared/api";
import {
  useResourceEditorState,
  type ResourceEditorControl,
} from "../../../shared/dialogs";
import { Timestamp } from "../../../shared/feedback";
import { CopyableId } from "../../../shared/identity";
import styles from "../memories.module.css";
import { MemoryRunLink } from "../run-link";

type Source = Schema["MemoryRecordSource"];

export function RecordSourceDialog({
  record,
  ...control
}: { record: Schema["MemoryRecordView"] } & ResourceEditorControl) {
  const { t } = useTranslation();
  const { modalProps } = useResourceEditorState(control);
  const source = record.provenance;
  return (
    <ModalFrame
      {...modalProps}
      title={t("Writing source")}
      description={t(
        "Writes confirmed by this platform. Direct changes in the memory provider may not be recorded.",
      )}
      closeLabel={t("Close")}
      size="lg"
    >
      {!source ? (
        <p className={styles.note}>{t("No source recorded")}</p>
      ) : (
        <div className={styles.sources}>
          {!source.content_matches && (
            <p role="status" className={styles.sourceWarning}>
              {t(
                "The current content differs from the last recorded write. These sources may no longer describe it.",
              )}
            </p>
          )}
          <section className={styles.source}>
            <h3>{t("Creation source")}</h3>
            {source.created ? (
              <SourceDetails source={source.created} />
            ) : (
              <p className={styles.note}>{t("No source recorded")}</p>
            )}
          </section>
          <section className={styles.source}>
            <h3>{t("Last platform-confirmed write")}</h3>
            <SourceDetails source={source.last_write} />
          </section>
        </div>
      )}
    </ModalFrame>
  );
}

function SourceDetails({ source }: { source: Source }) {
  const { t } = useTranslation();
  const ids = [
    [t("Principal ID"), source.principal_id],
    [t("Run ID"), source.run_id],
    [t("Attempt ID"), source.attempt_id],
    [t("Tool call ID"), source.tool_call_id],
  ] as const;
  return (
    <>
      <p className={styles.sourceLead}>
        {t(
          source.run_id
            ? "Written by an agent run"
            : "Written through the Console or API",
        )}
      </p>
      <dl className={styles.sourceFacts}>
        <dt>{t("Acting identity")}</dt>
        <dd>{source.principal_name ?? source.principal_id ?? t("Unknown")}</dd>
        <dt>{t("Confirmed at")}</dt>
        <dd>
          <Timestamp value={source.confirmed_at} />
        </dd>
        {source.run_id && (
          <>
            <dt>{t("Run")}</dt>
            <dd>
              <MemoryRunLink runId={source.run_id} explainUnavailable />
            </dd>
          </>
        )}
      </dl>
      <DisclosureSection title={t("Technical details")}>
        <dl className={styles.sourceFacts}>
          {ids.map(([label, value]) => (
            <Fragment key={label}>
              <dt>{label}</dt>
              <dd>{value ? <CopyableId value={value} /> : "—"}</dd>
            </Fragment>
          ))}
        </dl>
      </DisclosureSection>
    </>
  );
}

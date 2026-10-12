import { ModalFrame } from "a13n-ui";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../../shared/api";
import {
  useResourceEditorState,
  type ResourceEditorControl,
} from "../../../shared/dialogs";
import { Timestamp } from "../../../shared/feedback";
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
        <p>{t("No source recorded")}</p>
      ) : (
        <div className="grid gap-6">
          {!source.content_matches && (
            <p role="status">
              {t(
                "The current content differs from the last recorded write. These sources may no longer describe it.",
              )}
            </p>
          )}
          <section className="grid gap-2">
            <h3 className="font-medium">{t("Creation source")}</h3>
            {source.created ? (
              <SourceDetails source={source.created} />
            ) : (
              <p>{t("No source recorded")}</p>
            )}
          </section>
          <section className="grid gap-2">
            <h3 className="font-medium">
              {t("Last platform-confirmed write")}
            </h3>
            <SourceDetails source={source.last_write} />
          </section>
        </div>
      )}
    </ModalFrame>
  );
}

function SourceDetails({ source }: { source: Source }) {
  const { t } = useTranslation();
  return (
    <div className="grid gap-2">
      <p>
        {t(
          source.run_id
            ? "Written by an agent run"
            : "Written through the Console or API",
        )}
      </p>
      <p>
        {t("Acting identity")}:{" "}
        {source.principal_name ?? source.principal_id ?? t("Unknown")}
      </p>
      <p>
        {t("Confirmed at")}: <Timestamp value={source.confirmed_at} />
      </p>
      {source.run_id && (
        <MemoryRunLink runId={source.run_id} explainUnavailable />
      )}
      <details>
        <summary className="cursor-pointer">{t("Technical details")}</summary>
        <dl className="mt-2 grid gap-1 break-all text-sm">
          <dt>{t("Principal ID")}</dt>
          <dd>{source.principal_id ?? "—"}</dd>
          <dt>{t("Run ID")}</dt>
          <dd>{source.run_id ?? "—"}</dd>
          <dt>{t("Attempt ID")}</dt>
          <dd>{source.attempt_id ?? "—"}</dd>
          <dt>{t("Tool call ID")}</dt>
          <dd>{source.tool_call_id ?? "—"}</dd>
        </dl>
      </details>
    </div>
  );
}

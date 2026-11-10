import { useQuery } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { organizationQueries } from "./organizations-api";
import styles from "./organization-review.module.css";

export type ReviewAction = "checked" | "replace_all" | "discard";
export function ReviewDialog({
  candidate,
  mode,
  reviewed,
  pending,
  close,
  confirm,
}: {
  candidate: Schema["OrganizationCandidate"];
  mode: ReviewAction;
  reviewed: number;
  pending: boolean;
  close: () => void;
  confirm: () => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace();
  const [acknowledged, setAcknowledged] = useState(false);
  const replacement = useQuery({
    ...organizationQueries(client, workspace.id).replacement(
      candidate.organization_id,
      candidate.candidate_seq,
      candidate.target_seq,
    ),
    enabled:
      mode === "replace_all" &&
      candidate.candidate_seq !== null &&
      candidate.target_seq !== null,
    retry: false,
  });
  const title =
    mode === "replace_all"
      ? t("Replace entire target")
      : mode === "discard"
        ? t("Discard candidate")
        : t("Apply all changes");
  return (
    <ModalFrame
      open
      onOpenChange={(open) => {
        if (!open) close();
      }}
      title={title}
      closeLabel={t("Close")}
      description={
        mode === "replace_all"
          ? t(
              "All current target files will be replaced by the reviewed candidate. Target-only files will be deleted.",
            )
          : mode === "discard"
            ? t(
                "The target memory will not be changed. This candidate can no longer be applied.",
              )
            : t(
                "Apply the reviewed candidate and target sequences atomically. If either changed, reload and review again.",
              )
      }
      footer={
        <>
          <Button variant="outline" disabled={pending} onClick={close}>
            {t("Back to review")}
          </Button>
          <Button
            variant={mode === "checked" ? "default" : "destructive"}
            loading={pending}
            disabled={
              mode === "replace_all" &&
              (!replacement.data || replacement.isError || !acknowledged)
            }
            onClick={confirm}
          >
            {title}
          </Button>
        </>
      }
    >
      <div className={styles.confirmSummary}>
        <strong>
          {t("All {{count}} changed files", {
            count: candidate.changes.length,
          })}
        </strong>
        <p>
          {t("{{reviewed}} of {{total}} files reviewed", {
            reviewed,
            total: candidate.changes.length,
          })}
        </p>
        <dl className={styles.sequences}>
          <div>
            <dt>{t("Candidate")}</dt>
            <dd>{candidate.candidate_seq}</dd>
          </div>
          <div>
            <dt>{t("Target")}</dt>
            <dd>{candidate.target_seq}</dd>
          </div>
        </dl>
      </div>
      {mode === "replace_all" && (
        <div className={styles.effects}>
          <ErrorNotice
            error={replacement.error}
            retry={() => void replacement.refetch()}
          />
          {replacement.isPending ? (
            <Loading variant="code" />
          ) : (
            replacement.data && (
              <>
                {(
                  [
                    [
                      t("Target-only files to delete"),
                      replacement.data.deletions,
                    ],
                    [
                      t("Concurrent target edits to overwrite"),
                      replacement.data.overwrites,
                    ],
                    [
                      t("Target-deleted files to restore"),
                      replacement.data.restorations,
                    ],
                  ] as const
                ).map(([label, paths]) => (
                  <section key={label}>
                    <h4>
                      {label} <span>{paths.length}</span>
                    </h4>
                    {paths.length ? (
                      <ul>
                        {paths.map((path) => (
                          <li key={path}>{path}</li>
                        ))}
                      </ul>
                    ) : (
                      <p>{t("None")}</p>
                    )}
                  </section>
                ))}
                <label className={styles.acknowledge}>
                  <input
                    type="checkbox"
                    checked={acknowledged}
                    onChange={(event) => setAcknowledged(event.target.checked)}
                  />
                  {t("I understand these target changes will be replaced.")}
                </label>
              </>
            )
          )}
        </div>
      )}
    </ModalFrame>
  );
}

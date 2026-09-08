import { useState } from "react";
import { Dialog, Button } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { Composer } from "./composer";
import { OptionsComposer } from "./options";
import styles from "./conversations.module.css";

export function ContinueBranch({
  run,
  thread,
  accepted,
}: {
  run: Schema["RunResource"];
  thread: Schema["ThreadResource"];
  accepted: (receipt: Schema["RunAcceptanceReceipt"]) => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace(),
    [confirmed, setConfirmed] = useState(false);
  return (
    <Dialog
      title={t("Continue from this run")}
      description={t(
        "Move this thread forward from the selected completed run. A waiting branch will be abandoned.",
      )}
      closeLabel={t("Close")}
      trigger={<Button size="sm">{t("Continue from here")}</Button>}
    >
      <label className={styles.confirmation}>
        <input
          type="checkbox"
          checked={confirmed}
          onChange={(event) => setConfirmed(event.target.checked)}
        />
        {t("Use this run as the new parent for the thread.")}
      </label>
      <OptionsComposer
        commandBasis={thread.version}
        disabled={!confirmed}
        label={t("Continue from here")}
        submit={async (intent, key) =>
          accepted(
            data(
              await client.http.POST("/api/v1/runs/{source_run_id}/continue", {
                params: {
                  path: { source_run_id: run.id },
                  header: commandHeaders(workspace.id, key),
                },
                body: { ...intent, expected_thread_version: thread.version },
              }),
            ),
          )
        }
      />
    </Dialog>
  );
}
export function ContinueWithoutFeedback({
  run,
  thread,
  accepted,
}: {
  run: Schema["RunResource"];
  thread: Schema["ThreadResource"];
  accepted: (receipt: Schema["RunAcceptanceReceipt"]) => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace(),
    [confirmed, setConfirmed] = useState(false);
  return (
    <Dialog
      title={t("Continue with a new message")}
      description={t(
        "This rejects every pending approval and provides no response to pending tools and questions. The new message is submitted after resolving the entire waiting batch.",
      )}
      closeLabel={t("Close")}
      trigger={<Button size="sm">{t("Continue without feedback")}</Button>}
    >
      <label className={styles.confirmation}>
        <input
          type="checkbox"
          checked={confirmed}
          onChange={(event) => setConfirmed(event.target.checked)}
        />
        {t("Reject pending approvals and leave other actions unanswered.")}
      </label>
      <Composer
        disabled={!confirmed}
        label={t("Resolve and continue")}
        submit={async (input, key) => {
          const receipt = data(
            await client.http.POST("/api/v1/threads/{thread_id}/runs", {
              params: {
                path: { thread_id: thread.id },
                header: commandHeaders(workspace.id, key),
              },
              body: {
                expected_thread_version: thread.version,
                input,
                waiting_resolution: {
                  mode: "defaults",
                  sealed_state_digest_sha256: run.sealed_state_digest_sha256!,
                },
              },
            }),
          );
          if (receipt.run) accepted(receipt.run);
        }}
      />
    </Dialog>
  );
}

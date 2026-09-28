import { Button, Label, ModalFrame, Switch } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../../shared/api";
import { Composer } from "../composer";
import styles from "./cards.module.css";

/** Resolving the whole waiting batch by default is a decision, so it is confirmed. */
export function ContinueWithoutFeedback({
  run,
  thread,
  accepted,
}: {
  run: Schema["RunView"];
  thread: Schema["ThreadView"];
  accepted: (next: Schema["RunView"] | null) => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace(),
    [confirmed, setConfirmed] = useState(false);
  return (
    <ModalFrame
      trigger={
        <Button variant="ghost" type="button">
          {t("Continue without feedback")}
        </Button>
      }
      size="md"
      title={t("Continue with a new message")}
      description={t(
        "This rejects every pending approval and provides no response to pending tools and questions. The new message is submitted after resolving the entire waiting batch.",
      )}
      closeLabel={t("Close")}
    >
      <div className={styles.continuation}>
        <Label className={styles.cardSwitch}>
          <Switch checked={confirmed} onCheckedChange={setConfirmed} />
          {t("Reject pending approvals and leave other actions unanswered.")}
        </Label>
        <Composer
          disabled={!confirmed}
          label={t("Resolve and continue")}
          placeholder={t("Answer above, or send a new message")}
          submit={async (payload, key) => {
            const workspace_id = workspace.id;
            // Explicitly resolve the whole wait before submitting ordinary guidance.
            const resumed = data(
              await client
                .workspace(workspace_id)
                .POST("/api/v1/runs/{run_id}/resume", {
                  params: {
                    path: { run_id: run.id },
                    header: commandHeaders(`${key}:resume`),
                  },
                  body: { answers: [] },
                }),
            );
            const receipt = data(
              await client
                .workspace(workspace_id)
                .POST("/api/v1/threads/{thread_id}/inbox", {
                  params: {
                    path: { thread_id: thread.id },
                    header: commandHeaders(key),
                  },
                  body: {
                    kind: "message",
                    delivery: "steer",
                    payload,
                    agent_id: run.agent_id,
                  },
                }),
            );
            accepted(receipt.run ?? resumed);
          }}
        />
      </div>
    </ModalFrame>
  );
}

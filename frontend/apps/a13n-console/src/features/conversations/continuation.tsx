import { Button, Checkbox, Label } from "a13n-ui";

import { ModalFrame } from "a13n-ui";

import { useState } from "react";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { Composer } from "./composer";

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
    <ModalFrame
      trigger={
        <Button size="sm" variant="outline" type="button">
          {t("Continue without feedback")}
        </Button>
      }
      size={"md"}
      title={t("Continue with a new message")}
      description={t(
        "This rejects every pending approval and provides no response to pending tools and questions. The new message is submitted after resolving the entire waiting batch.",
      )}
      closeLabel={t("Close")}
    >
      <Label className="flex items-center gap-2">
        <Checkbox checked={confirmed} onCheckedChange={setConfirmed} />
        {t("Reject pending approvals and leave other actions unanswered.")}
      </Label>
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
    </ModalFrame>
  );
}

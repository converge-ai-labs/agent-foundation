import { McpElicitationForm, type McpElicitationResponse } from "a13n-ui";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import type { Schema } from "../../../shared/api";
import { ErrorNotice } from "../../../shared/feedback";
import { useSessionStream } from "../session-stream";
import styles from "./mcp-inputs.module.css";

export function McpInputs({ threadId }: { threadId: string }) {
  const stream = useSessionStream();
  const requests = stream?.interactions.get(threadId);
  if (!requests?.length) return null;
  return (
    <div className={styles.requests}>
      {requests.map((request) => (
        <McpInput
          key={request.request_id}
          request={request}
          connected={stream?.state === "live"}
        />
      ))}
    </div>
  );
}

function McpInput({
  request,
  connected,
}: {
  request: Schema["McpElicitation"];
  connected: boolean;
}) {
  const client = useClient();
  const { workspace } = useWorkspace();
  const { t } = useTranslation();
  const answer = useMutation({
    mutationFn: async (body: McpElicitationResponse) => {
      await client
        .workspace(workspace.id)
        .POST("/api/v1/runs/{run_id}/mcp-inputs/{request_id}/response", {
          body,
          params: {
            path: { run_id: request.run_id!, request_id: request.request_id },
          },
        });
    },
  });
  return (
    <McpElicitationForm
      request={request}
      t={t}
      send={answer.mutate}
      locked={!connected || answer.isPending || answer.isSuccess}
      feedback={answer.error && <ErrorNotice error={answer.error} />}
    />
  );
}

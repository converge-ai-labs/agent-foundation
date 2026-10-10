import { Button, DisclosureSection, Label, ModalFrame, Switch } from "a13n-ui";
import { GitBranchIcon } from "@phosphor-icons/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useLocation, useNavigate } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { ErrorNotice, StatePill } from "../../shared/feedback";
import { FormActions, TextAreaField } from "../../shared/forms";
import { useIdempotency } from "../../shared/idempotency";
import { AgentAvatar } from "../agents/avatar";
import { useAgent } from "../agents/queries";
import {
  conversationKeys,
  invalidateConversation,
  isActiveRun,
  runPath,
  type ViewLevel,
} from "./api";
import { runRequest } from "./request";
import styles from "./fork.module.css";

/** One Run boundary, shared by Chat, Debug and the historical-run dock. */
export function ForkRun({
  run,
  thread,
  level,
  index,
  compact = false,
}: {
  run: Schema["RunView"];
  thread: Schema["ThreadView"];
  level: ViewLevel;
  index?: number | null;
  compact?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const { can, workspace, basePath } = useWorkspace();
  const client = useClient();
  const cache = useQueryClient();
  const navigate = useNavigate();
  const { search } = useLocation();
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [fresh, setFresh] = useState(false);
  const idempotency = useIdempotency();
  const agent = useAgent(open ? run.agent_id : undefined);
  const allowed = can("run");
  const sealed = !!run.sealed_at && !isActiveRun(run.status);
  const reason = !allowed
    ? t("Run permission is required to create a branch.")
    : !sealed
      ? t("Create a branch after this run stops executing.")
      : undefined;
  const mutation = useMutation({
    mutationFn: async () => {
      if (reason) throw new Error(reason);
      if (!message.trim()) throw new Error(t("Write a message first."));
      const body: Schema["Fork"] = {
        kind: "message",
        delivery: "next_run",
        agent_id: run.agent_id,
        agent_revision_id: run.agent_revision_id,
        options: run.options,
        payload: { content: [{ type: "text", text: message }] },
        fresh_environments: fresh,
      };
      return data(
        await client
          .workspace(workspace.id)
          .POST("/api/v1/runs/{run_id}/fork", {
            params: {
              path: { run_id: run.id },
              header: commandHeaders(
                idempotency.forBody({ runId: run.id, body }),
              ),
            },
            body,
          }),
      );
    },
    onSuccess: (receipt) => {
      const keys = conversationKeys(workspace.id);
      cache.setQueryData(keys.thread(receipt.thread.id), receipt.thread);
      cache.setQueryData<Schema["ThreadView"][]>(
        keys.threads(receipt.thread.session_id),
        (threads) =>
          threads && [
            ...threads.filter((thread) => thread.id !== receipt.thread.id),
            receipt.thread,
          ],
      );
      if (receipt.run)
        cache.setQueryData(keys.run(receipt.run.id), receipt.run);
      void invalidateConversation(cache, workspace.id, {
        sessionId: receipt.thread.session_id,
        threadId: receipt.thread.id,
        runId: receipt.run?.id,
      });
      // The new Thread owns its own lineage. Do not append it to the old
      // transcript as a same-thread continuation.
      const params = new URLSearchParams(search);
      params.set("view", level);
      const path = receipt.run
        ? runPath(basePath, { ...receipt.run, run_id: receipt.run.id })
        : `${basePath}/sessions/${receipt.thread.session_id}/threads/${receipt.thread.id}`;
      setOpen(false);
      setMessage("");
      setFresh(false);
      idempotency.reset();
      navigate(`${path}?${params}`);
    },
  });
  return (
    <ModalFrame
      open={open}
      onOpenChange={(next, details) => {
        if (mutation.isPending) details.cancel();
        else setOpen(next);
      }}
      trigger={
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={!!reason}
          title={reason}
        >
          <GitBranchIcon size={14} aria-hidden="true" />
          {t(compact ? "Create branch" : "Create branch from here")}
        </Button>
      }
      title={t("Create branch from here")}
      description={t(
        "Continue from this run's saved history with a new message. The new branch stays in this session; the original conversation can continue.",
      )}
      closeLabel={t("Close")}
    >
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          if (!mutation.isPending) mutation.mutate();
        }}
      >
        <div className={styles.source}>
          <div className={styles.sourceHeading}>
            <GitBranchIcon size={16} aria-hidden="true" />
            <strong>
              {index ? t("Run {{index}}", { index }) : t("Branch point")}
            </strong>
            <StatePill state={run.status} />
            <time dateTime={run.created_at}>
              {new Intl.DateTimeFormat(i18n.resolvedLanguage, {
                dateStyle: "medium",
                timeStyle: "short",
              }).format(new Date(run.created_at))}
            </time>
          </div>
          <p className={styles.preview}>{runRequest(run, thread).text}</p>
        </div>
        {run.status === "waiting" && (
          <p className={styles.warning}>
            {t(
              "Pending approvals will be denied and unanswered calls will fail in the new branch. The original conversation keeps waiting.",
            )}
          </p>
        )}
        {["failed", "cancelled"].includes(run.status) && (
          <p className={styles.note}>
            {t(
              "This branch continues from the last saved checkpoint. Unsaved work may not be included.",
            )}
          </p>
        )}
        <TextAreaField
          label={t("What would you like to continue in the new branch?")}
          value={message}
          onChange={setMessage}
          disabled={mutation.isPending}
          required
          rows={4}
        />
        <div className={styles.agent}>
          <AgentAvatar
            name={agent.data?.name ?? t("Agent")}
            id={run.agent_id}
            url={agent.data?.image_url}
          />
          <span>{agent.data?.name ?? t("Agent")}</span>
          <span className={styles.note}>
            {t("Uses this run's agent version and options")}
          </span>
        </div>
        <div className={styles.environment}>
          <strong>
            {t(
              fresh
                ? "New file environment, shared memory"
                : "Shared file environments and memory",
            )}
          </strong>
          <p className={styles.note}>
            {t(
              fresh
                ? "Uses the agent's default environment, if configured. Existing files are not copied. Memory remains shared."
                : "Branches use the same files and memory. Changes can be visible in both conversations.",
            )}
          </p>
          <DisclosureSection title={t("Environment options")}>
            <Label className={styles.environmentChoice}>
              <Switch
                checked={fresh}
                onCheckedChange={setFresh}
                disabled={mutation.isPending}
              />
              {t("Use a new environment")}
            </Label>
          </DisclosureSection>
        </div>
        <ErrorNotice error={mutation.error} />
        {reason && <p className={styles.warning}>{reason}</p>}
        <FormActions
          pending={mutation.isPending}
          disabled={!message.trim() || !!reason || mutation.isPending}
          label={t("Create branch and send")}
          onCancel={() => setOpen(false)}
        />
      </form>
    </ModalFrame>
  );
}

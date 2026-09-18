import { Link } from "react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useMaintenance, useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ConfirmAction } from "./confirm-action";
import { ErrorNotice, Panel } from "./ui";
import styles from "./workbench.module.css";

const labels: Record<
  NonNullable<Schema<"MaintenanceView">["phase"]>,
  string
> = {
  idle: "Ready for work",
  draining: "Waiting for tasks to pause",
  paused: "Tasks paused — ready for a normal shutdown",
  stopping: "Saving the update handoff",
  restoring: "Restoring tasks after update",
  finished: "Update recovery finished",
  blocked: "Update recovery needs attention",
};

export function MaintenanceBanner() {
  const maintenance = useMaintenance();
  const state = maintenance.data;
  if (!state || state.phase === "idle") return null;
  return (
    <div className={styles.notice} role="status">
      <strong>{labels[state.phase ?? "idle"]}</strong>
      <p>
        {state.phase === "paused"
          ? "Stop the server normally, update it externally, then start WebUI with the same data directory. Do not force-kill it."
          : state.phase === "draining"
            ? "Current model requests and tools are finishing. New messages and answers are paused; stopping now will not enable automatic recovery."
            : (state.message ??
              "No new messages or answers are accepted during recovery.")}
      </p>
      <Link to="/settings#update-maintenance">Update details</Link>
    </div>
  );
}

export function MaintenanceSettings() {
  const maintenance = useMaintenance();
  const { client } = useTransport();
  const queries = useQueryClient();
  const action = useMutation({
    mutationFn: (operation: "prepare" | "cancel" | "dismiss") => {
      if (operation === "prepare")
        return result(client.POST("/api/maintenance/prepare"));
      if (operation === "cancel")
        return result(client.POST("/api/maintenance/cancel"));
      return result(
        client.POST("/api/maintenance/dismiss", {
          body: { previous_instance_stopped: true },
        }),
      );
    },
    onSuccess: (state) => {
      queries.setQueryData(["maintenance"], state);
      void queries.invalidateQueries({ queryKey: ["threads"] });
    },
  });
  const state = maintenance.data;
  return (
    <section id="update-maintenance">
      <Panel title="Planned server update">
        <p>
          Pause tasks at their next model-request boundary before updating this
          server. This does not install software or restart the server, and does
          not recover a crash or forced shutdown.
        </p>
        <ErrorNotice
          error={maintenance.error || action.error}
          retry={() => void maintenance.refetch()}
        />
        {state && (
          <>
            <strong>{labels[state.phase ?? "idle"]}</strong>
            {state.message && <p>{state.message}</p>}
            {state.phase === "draining" && (
              <p>
                Wait for all tasks to pause. Long-running tools are not
                force-cancelled; you can cancel preparation and continue
                working.
              </p>
            )}
            {state.phase === "paused" && (
              <p>
                All tasks are paused in memory. A normal server shutdown must
                finish saving and Environment cleanup before the handoff is
                ready. Start the updated WebUI sequentially with the same data
                directory.
              </p>
            )}
            {!!(state.tasks ?? []).length && (
              <details className={styles.details}>
                <summary>
                  {
                    (state.tasks ?? []).filter(
                      (task) => task.state === "paused",
                    ).length
                  }{" "}
                  paused · {(state.tasks ?? []).length} tasks
                </summary>
                <ul>
                  {(state.tasks ?? []).map((task) => (
                    <li key={task.thread_id}>
                      <Link to={`/threads/${task.thread_id}`}>
                        {task.thread_id}
                      </Link>
                      : {task.state}
                      {task.message && <p>{task.message}</p>}
                      {task.resumed_execution_id && (
                        <p>Successor: {task.resumed_execution_id}</p>
                      )}
                    </li>
                  ))}
                </ul>
              </details>
            )}
            <div className={styles.actions}>
              {(state.phase === "idle" || state.phase === "finished") && (
                <ConfirmAction
                  trigger={
                    <Button
                      variant="outline"
                      disabled={!state.enabled || action.isPending}
                    >
                      Prepare server update
                    </Button>
                  }
                  title="Pause tasks for a server update?"
                  description="This affects everyone using this server. New messages, steering, and answers will be rejected while active tasks finish their current model request and tools. You can cancel preparation before stopping the server."
                  confirmLabel="Prepare update"
                  onConfirm={() => action.mutate("prepare")}
                />
              )}
              {state.can_cancel && (
                <Button
                  variant="outline"
                  disabled={action.isPending}
                  onClick={() => action.mutate("cancel")}
                >
                  Cancel preparation
                </Button>
              )}
              {state.can_dismiss && (
                <ConfirmAction
                  trigger={
                    <Button variant="ghost" disabled={action.isPending}>
                      Dismiss handoff
                    </Button>
                  }
                  title="Discard this update handoff?"
                  description="Confirm the previous server instance has stopped. This removes the automatic recovery handoff, not saved conversation history. It does not retry or undo any task or external operation. Inspect blocked tasks before starting new work."
                  confirmLabel="Previous instance stopped — dismiss"
                  destructive={state.phase === "blocked"}
                  onConfirm={() => action.mutate("dismiss")}
                />
              )}
            </div>
            <p>
              Unanswered questions remain waiting. Browser drafts and Run-local
              shell handles are not part of this handoff.
            </p>
          </>
        )}
      </Panel>
    </section>
  );
}

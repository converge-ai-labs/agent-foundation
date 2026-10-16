import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  ModalFrame,
  SettingsRow,
  SettingsSection,
  Skeleton,
  StatusPill,
} from "a13n-ui";
import { useSources, useTransport } from "../transport/context";
import { result } from "../transport/client";
import { ErrorNotice, PageHeader } from "../shell/ui";
import { SourceDocument } from "./sources";
import { useEnvdStatus } from "./envd-fields";
import styles from "../shell/workbench.module.css";
import layout from "./runtime.module.css";

export function RuntimePage() {
  const sources = useSources();
  const root = sources.data?.sources.find(
    (item) => item.resource_kind === "root",
  );
  return (
    <div className={layout.page}>
      <PageHeader
        title="Runtime"
        description="Manage envd and check this Host’s capabilities."
      />
      <EnvdRuntime />
      {sources.isPending ? (
        <Skeleton className="h-64 w-full" />
      ) : (
        root && (
          <SourceDocument
            key={root.relative_path}
            path={root.relative_path}
            title="Configuration"
            embedded
            section="envd"
          />
        )
      )}
      <ErrorNotice error={sources.error} />
      <SettingsSection title="Next steps" variant="plain">
        <SettingsRow
          label="Choose agent execution"
          description="Choose Direct, Sandbox or Computer Use for new conversations."
        >
          <Link to="/settings/environments">Environments →</Link>
        </SettingsRow>
        <SettingsRow
          label="Set up your model"
          description="Continue first-use setup when your runtime is ready."
        >
          <Link to="/setup">Continue setup →</Link>
        </SettingsRow>
      </SettingsSection>
      <RuntimeDetails />
    </div>
  );
}

type Action = "execution" | "desktop" | "restart" | "reinstall" | "stop";
type Confirmation = "restart" | "reinstall" | "stop";
const confirmationLabels = {
  restart: "Restart runtime",
  reinstall: "Reinstall & restart",
  stop: "Stop all runtimes",
};

export function EnvdRuntime() {
  const { client } = useTransport();
  const queries = useQueryClient();
  const runtime = useEnvdStatus();
  const state = runtime.data;
  const updateAvailable =
    state?.source === "managed" &&
    !!state.required_version &&
    state.required_version !== "0.0.0" &&
    !state.installed &&
    !!state.cached_versions?.length;
  const [notice, setNotice] = useState("");
  const [checkingSandbox, setCheckingSandbox] = useState(false);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  const action = useMutation({
    mutationFn: async (mode: Action) => {
      setNotice("");
      const pending = new AbortController();
      controller.current = pending;
      try {
        if (mode === "stop") {
          await client.POST("/api/envd/stop", { body: { confirm: true } });
          return "Runtimes stopped.";
        }
        const readiness =
          mode === "restart" || mode === "reinstall"
            ? await result(
                client.POST(
                  mode === "restart"
                    ? "/api/envd/restart"
                    : "/api/envd/reinstall",
                  { body: { confirm: true }, signal: pending.signal },
                ),
              )
            : await result(
                client.POST("/api/envd/start", {
                  body: { computer_use: mode === "desktop" },
                  signal: pending.signal,
                }),
              );
        pending.signal.throwIfAborted();
        if (!readiness.ready)
          throw new Error(
            [readiness.message, ...(readiness.instructions ?? [])].join(" "),
          );
        if (mode === "desktop") return "Computer Use ready.";
        setCheckingSandbox(true);
        const sandbox = await result(
          client.POST("/api/environments/preflight", {
            body: { profile_id: "environment-sandbox" },
            signal: pending.signal,
          }),
        );
        pending.signal.throwIfAborted();
        if (!sandbox.ready)
          throw new Error(`Runtime ready. Sandbox: ${sandbox.message}`);
        return "Runtime and Sandbox ready.";
      } catch (error) {
        if (pending.signal.aborted)
          throw new Error("Preparation cancelled. Check again to continue.");
        throw error;
      } finally {
        setCheckingSandbox(false);
        controller.current = null;
      }
    },
    onSuccess: setNotice,
    onSettled: () => {
      void queries.invalidateQueries({ queryKey: ["envd-runtime"] });
    },
  });
  const busy =
    action.isPending ||
    !!state?.preparing ||
    (!!state && state.phase !== "idle");
  const blocked = !state || busy || state.restart_required;
  const progress = checkingSandbox
    ? "Checking Sandbox isolation…"
    : state?.phase === "downloading"
      ? `Downloading envd · ${((state.downloaded_bytes ?? 0) / 1048576).toFixed(1)} MB${state.total_bytes ? ` / ${(state.total_bytes / 1048576).toFixed(1)} MB` : ""}`
      : state?.phase === "verifying"
        ? "Verifying runtime…"
        : action.variables === "stop"
          ? "Stopping runtimes…"
          : "Preparing runtime and checking readiness…";
  return (
    <>
      <SettingsSection
        title="Local runtime"
        description={state?.host ?? "Loading Host…"}
      >
        <SettingsRow
          label="envd"
          description={
            state?.source === "custom"
              ? "Custom executable · managed on this Host"
              : `Managed by Harness UI · ${state?.required_version ?? "loading version…"}`
          }
        >
          <StatusPill variant={state?.running_devices ? "success" : "neutral"}>
            {!state
              ? "Loading…"
              : state.running_devices
                ? `${state.running_devices} running`
                : state.installed
                  ? "Installed · stopped"
                  : updateAvailable
                    ? "Update required"
                    : "Not installed"}
          </StatusPill>
        </SettingsRow>
        <div className={layout.actions}>
          <Button
            size="sm"
            variant="outline"
            disabled={blocked}
            onClick={() => action.mutate("execution")}
          >
            {action.isPending && action.variables === "execution"
              ? "Preparing…"
              : state?.running_devices
                ? "Check"
                : state?.installed
                  ? "Start"
                  : updateAvailable
                    ? "Update & start"
                    : "Install & start"}
          </Button>
          {!!state?.instances?.length && (
            <>
              <Button
                size="sm"
                variant="outline"
                disabled={blocked}
                onClick={() => setConfirmation("restart")}
              >
                Restart
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={busy}
                onClick={() => setConfirmation("stop")}
              >
                Stop
              </Button>
            </>
          )}
          {action.isPending && action.variables !== "stop" && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => controller.current?.abort()}
            >
              Cancel preparation
            </Button>
          )}
        </div>
      </SettingsSection>
      {!state?.installed && state && (
        <p className={layout.hint}>
          {updateAvailable
            ? `Update envd to ${state.required_version} for this Harness UI version.`
            : "Install envd to check Full Control and Sandbox automatically."}
        </p>
      )}
      {busy && (
        <p role="status" className={layout.hint}>
          {progress}
          {action.variables === "desktop" &&
            " Approve the permission prompt on the Host; this can take up to two minutes."}
        </p>
      )}
      {notice && (
        <p role="status" className={layout.hint}>
          {notice}
        </p>
      )}
      <ErrorNotice
        error={runtime.error || action.error || state?.error}
        retry={() => void runtime.refetch()}
      />
      {state?.restart_required && (
        <div role="status" className={styles.notice}>
          <strong>Restart Harness UI to apply saved configuration</strong>
          <p>
            Restart the server with the same launch command to load these
            settings.
          </p>
        </div>
      )}
      <SettingsSection
        title="Capabilities"
        description="Full Control and Sandbox are checked together."
      >
        <SettingsRow
          label="Full Control"
          description="File and command execution. Checked when you start the runtime."
        >
          {readiness("environment-envd")}
        </SettingsRow>
        <SettingsRow
          label="Sandbox"
          description="Checked automatically. Each Project also checks its own workspace."
        >
          {readiness("environment-sandbox")}
        </SettingsRow>
        <SettingsRow
          label="Computer Use"
          description={
            state?.computer_use
              ? "Screen observation and input require permission on this Host."
              : "Enable Desktop capability in Configuration, save, then restart Harness UI."
          }
        >
          {state?.computer_use ? (
            <div className={layout.controls}>
              {readiness("environment-desktop")}
              <Button
                size="sm"
                variant="outline"
                disabled={blocked}
                onClick={() => action.mutate("desktop")}
              >
                Check permissions
              </Button>
            </div>
          ) : (
            <StatusPill>Disabled</StatusPill>
          )}
        </SettingsRow>
      </SettingsSection>
      <SettingsSection
        title="Version & updates"
        description="The envd version selected by this Harness UI installation."
      >
        <SettingsRow
          label="Required version"
          description={
            state?.source === "custom"
              ? "Custom executables are updated on the Host. Restart the runtime after replacing the file."
              : state?.installed
                ? "Reinstall this version and restart envd."
                : "Downloaded when you install or update envd."
          }
        >
          <div className={layout.controls}>
            <span>{state?.required_version ?? "Unavailable"}</span>
            {state?.source === "managed" &&
              state.installed &&
              state.required_version &&
              state.required_version !== "0.0.0" && (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={blocked}
                  onClick={() => setConfirmation("reinstall")}
                >
                  Reinstall…
                </Button>
              )}
          </div>
        </SettingsRow>
        {!!state?.cached_versions?.length && (
          <SettingsRow label="Cached versions">
            <span>{state.cached_versions.join(", ")}</span>
          </SettingsRow>
        )}
      </SettingsSection>
      <ModalFrame
        open={confirmation !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmation(null);
        }}
        title={
          confirmation === "stop"
            ? "Stop all local envd runtimes?"
            : confirmation === "reinstall"
              ? "Reinstall the compatible envd release?"
              : "Restart local envd?"
        }
        description={
          confirmation === "stop"
            ? "Interrupts local envd commands in all conversations. Workspace files are kept."
            : "Closes local envd Sessions in all conversations, then checks Full Control and Sandbox. Workspace files are kept."
        }
        closeLabel="Close confirmation"
        footer={
          <>
            <Button variant="outline" onClick={() => setConfirmation(null)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                if (confirmation) action.mutate(confirmation);
                setConfirmation(null);
              }}
            >
              {confirmation ? confirmationLabels[confirmation] : "Confirm"}
            </Button>
          </>
        }
      >
        {confirmation === "reinstall" && (
          <p>
            Downloads release {state?.required_version}. If download or
            verification fails, runtimes remain stopped and the previous
            executable is kept.
          </p>
        )}
      </ModalFrame>
    </>
  );
  function readiness(profile: string) {
    const checked = state?.readiness?.find(
      (item) => item.profile_id === profile,
    );
    return (
      <StatusPill
        variant={checked ? (checked.ready ? "success" : "warning") : "neutral"}
      >
        {checked ? (checked.ready ? "Checked" : "Not ready") : "Not checked"}
      </StatusPill>
    );
  }
}

function RuntimeDetails() {
  const { data: state } = useEnvdStatus();
  return (
    <details className={styles.details}>
      <summary>Installation & instance details</summary>
      <dl className={layout.metadata}>
        <dt>Verified managed release</dt>
        <dd>{state?.verified_version ?? "Not verified"}</dd>
        <dt>Active executable</dt>
        <dd>{state?.executable ?? "Not installed"}</dd>
      </dl>
      {state?.instances?.map((instance) => (
        <div key={instance.device_id} className={styles.resourceRow}>
          <div className="min-w-0">
            <strong>
              {instance.sandbox ? "Sandbox" : "Full Control"}
              {instance.computer_use ? " · Computer Use" : ""}
            </strong>
            <small className="break-all">{instance.device_id}</small>
            {instance.roots.map((root) => (
              <small className="break-all" key={root}>
                {root}
              </small>
            ))}
          </div>
          <StatusPill variant={instance.running ? "success" : "neutral"}>
            {instance.running ? "Running" : "Not running"}
          </StatusPill>
        </div>
      ))}
    </details>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  Dialog,
  DialogPopup,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogPanel,
  DialogFooter,
  FormField,
  Textarea,
} from "a13n-ui";
import { ArrowRight, Check, ShieldCheck, Desktop } from "@phosphor-icons/react";
import { useTransport } from "../transport/context";
import { ApiError, result, type Schema } from "../transport/client";
import { ErrorNotice, TextField } from "../shell/ui";
import {
  ModelEditor,
  type ModelEditorDraft,
} from "../configuration/model-editor";
import {
  dismissWizard,
  finishWizard,
  readWizardDraft,
  saveWizardDraft,
  type WizardDraft,
} from "./wizard-state";
import { ShellReviewModel } from "./shell-review";
import { LiveOptIn } from "./live-opt-in";
import styles from "./wizard.module.css";
type Selection = Schema<"SetupSelection">;

export function SetupWizard({ status }: { status: Schema<"SetupStatus"> }) {
  const { client } = useTransport();
  const queries = useQueryClient();
  const navigate = useNavigate();
  const scope = status.draft_scope!;
  const [draft, setDraft] = useState<WizardDraft>(
    () =>
      readWizardDraft(scope) ?? {
        version: 2,
        step: 0,
        selection: {
          environment_profile: "environment-native",
          ...status.defaults,
          include_default_subagents: true,
          default_agent: "agent-primary",
        },
        threadId: `thread_${crypto.randomUUID().replaceAll("-", "")}`,
      },
  );
  const [modelReady, setModelReady] = useState(false);
  const changeModelDraft = useCallback(
    (modelDraft: ModelEditorDraft) =>
      setDraft((value) => ({ ...value, modelDraft })),
    [],
  );
  const [error, setError] = useState<unknown>();
  const [storageAvailable, setStorageAvailable] = useState(true);
  const [phase, setPhase] = useState("");
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const patch = (change: Partial<WizardDraft>) =>
    setDraft((value) => ({ ...value, ...change }));
  const changeSelection = (change: Partial<Selection>) =>
    setDraft((value) => ({
      ...value,
      selection: { ...value.selection, ...change },
    }));
  useEffect(() => {
    setStorageAvailable(saveWizardDraft(scope, draft));
  }, [scope, draft]);
  useEffect(() => {
    heading.current?.focus();
  }, [draft.step]);
  const selection: Selection = draft.selection;
  const apiKey = selection.model?.authentication.kind === "api_key";
  const steps = apiKey
    ? ["Model", "Shell review", "Workspace"]
    : ["Model", "Workspace"];
  const workspaceStep = steps.length - 1;
  const reviewStep = apiKey && draft.step === 1;
  const workspaceActive = draft.step === workspaceStep && !draft.pending;
  const preview = useQuery({
    queryKey: ["setup-preview", scope, selection],
    queryFn: ({ signal }) =>
      result(client.POST("/api/setup/preview", { body: selection, signal })),
    enabled: workspaceActive,
    retry: false,
    staleTime: Infinity,
    gcTime: 0,
  });
  const currentPreview = preview.data;
  const sandbox = selection.environment_profile === "environment-sandbox";
  const targets =
    workspaceActive && sandbox ? currentPreview?.project_paths : undefined;
  const [cancelledTargets, setCancelledTargets] = useState<string>();
  const targetKey = JSON.stringify(targets);
  const cancelled = cancelledTargets != null && cancelledTargets === targetKey;
  const sandboxCheck = useQuery({
    queryKey: ["setup-sandbox", scope, targets],
    queryFn: async ({ signal }) => {
      const checks: Record<string, Schema<"EnvironmentReadiness">> = {};
      for (const path of targets ?? []) {
        checks[path] = await result(
          client.POST("/api/environments/preflight", {
            body: { profile_id: "environment-sandbox", project_path: path },
            signal,
          }),
        );
      }
      return checks;
    },
    enabled: !!targets?.length && !cancelled,
    retry: false,
    staleTime: Infinity,
    gcTime: 0,
  });
  const readiness = sandboxCheck.data ?? {};
  const sandboxReady =
    !!targets?.length &&
    !sandboxCheck.isFetching &&
    targets.every((path) => readiness[path]?.ready);
  const runtime = useQuery({
    queryKey: ["envd-runtime"],
    queryFn: ({ signal }) => result(client.GET("/api/envd", { signal })),
    enabled: workspaceActive && sandbox,
    refetchInterval: sandboxCheck.isFetching ? 1000 : false,
  });
  const preparing = preview.isFetching || (sandbox && sandboxCheck.isFetching);
  const preparationError = preview.error || (sandbox && sandboxCheck.error);
  const preparationStatus = preview.isFetching
    ? "Checking configuration…"
    : sandbox && sandboxCheck.isFetching
      ? runtime.data?.phase === "downloading"
        ? `Downloading envd… ${Math.round((runtime.data.downloaded_bytes ?? 0) / 1024 / 1024)} MB${runtime.data.total_bytes ? ` / ${Math.round(runtime.data.total_bytes / 1024 / 1024)} MB` : ""}`
        : runtime.data?.phase === "verifying"
          ? "Verifying envd download…"
          : runtime.data?.phase === "checking"
            ? "Checking envd installation…"
            : runtime.data?.installed
              ? "Checking Sandbox isolation…"
              : "Preparing envd download…"
      : cancelled
        ? "Preparation cancelled. Retry or choose Full Control."
        : preparationError
          ? "Setup needs attention. Review the error above and retry."
          : sandbox && !sandboxReady
            ? "Sandbox is not ready. Retry or choose Full Control."
            : currentPreview
              ? "Ready to save"
              : "Checking configuration…";

  const run = async (operation: () => Promise<void>) => {
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy(true);
    setError(undefined);
    try {
      await operation();
    } catch (failure) {
      if (!(failure instanceof DOMException && failure.name === "AbortError"))
        setError(failure);
    } finally {
      busyRef.current = false;
      setBusy(false);
      setPhase("");
    }
  };
  const reconcile = async (pending: NonNullable<WizardDraft["pending"]>) => {
    setPhase("Checking saved files…");
    for (const [path, expected] of Object.entries(pending.files)) {
      const saved = await result(
        client.GET("/api/configuration/sources/{relative_path}", {
          params: { path: { relative_path: path } },
        }),
      );
      if (saved.content !== expected)
        throw new Error(
          "Some files are missing or changed. Review the changes before saving again. Existing resources are kept.",
        );
    }
    const saved = await result(
      client.GET("/api/setup", { params: { query: { rediscover: true } } }),
    );
    if (
      saved.needed ||
      saved.diagnostic ||
      saved.default_agent !== pending.selection.default_agent
    )
      throw new Error(
        "Check the saved configuration files before trying again.",
      );
  };
  const openConversation = async (
    pending: NonNullable<WizardDraft["pending"]>,
  ) => {
    setPhase("Opening your first chat…");
    const lookup = async () => {
      try {
        return await result(
          client.GET("/api/threads/{thread_id}", {
            params: { path: { thread_id: draft.threadId } },
          }),
        );
      } catch (failure) {
        if (failure instanceof ApiError && failure.code === "thread_missing")
          return null;
        throw failure;
      }
    };
    let found = await lookup();
    if (!found) {
      try {
        await result(
          client.POST("/api/threads", {
            body: {
              thread_id: draft.threadId,
              defaults: {
                agent_id: pending.selection.default_agent,
                project_id: pending.selection.project ?? null,
                environment_profile_id: pending.selection.environment_profile,
              },
            },
          }),
        );
      } catch (failure) {
        found = await lookup();
        if (!found) throw failure;
      }
    }
    found = await lookup();
    const configured = found?.thread.configuration;
    if (
      !configured ||
      configured.agent_source.kind !== "agent" ||
      configured.agent_source.id !== pending.selection.default_agent ||
      configured.environment_profile_id !==
        pending.selection.environment_profile ||
      (configured.project_id ?? null) !== (pending.selection.project ?? null)
    )
      throw new Error(
        "The first chat already uses different settings. Check its agent, project and environment before continuing. Setup hasn't changed it.",
      );
    finishWizard(scope);
    navigate(`/threads/${encodeURIComponent(draft.threadId)}?compose=1`, {
      replace: true,
    });
    void queries.invalidateQueries();
  };
  const save = () =>
    run(async () => {
      if (
        !currentPreview ||
        preparing ||
        preparationError ||
        (sandbox && !sandboxReady)
      )
        return;
      const pending = { selection, files: currentPreview.files };
      const next = { ...draft, pending };
      // Persist the exact publication intent before sending. Reload never resubmits it.
      if (!saveWizardDraft(scope, next))
        throw new Error(
          "Enable browser storage before saving. It lets setup recover if the connection is interrupted.",
        );
      setDraft(next);
      setPhase("Saving configuration…");
      try {
        const publication = await result(
          client.POST("/api/setup/apply", { body: { selection } }),
        );
        if (!publication.completed)
          throw new Error(
            publication.error_message || "Setup was only partially saved.",
          );
      } catch {
        // A transport failure is not evidence of rollback. Reconcile ordinary files.
      }
      await reconcile(pending);
      await openConversation(pending);
    });
  const close = () => {
    if (busyRef.current) return;
    dismissWizard(scope);
    navigate("/?setup=later", { replace: true });
  };
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <DialogPopup
        initialFocus={heading}
        finalFocus={false}
        className={styles.wizard}
        closeProps={{
          "aria-label": "Set up later",
          disabled: busy,
        }}
      >
        <DialogHeader>
          <DialogTitle className={styles.title}>Set up Harness</DialogTitle>
          <DialogDescription>
            Connect a model and choose where it can work.
          </DialogDescription>
          <ol className={styles.steps} aria-label="Setup progress">
            {steps.map((step, index) => (
              <li
                key={step}
                aria-current={index === draft.step ? "step" : undefined}
                data-complete={index < draft.step}
              >
                <span>
                  {index < draft.step ? <Check size={14} /> : index + 1}
                </span>
                {step}
              </li>
            ))}
          </ol>
        </DialogHeader>
        <DialogPanel scrollFade={false}>
          <div className={styles.content}>
            <h2 ref={heading} tabIndex={-1}>
              {draft.step === 0
                ? "Choose your everyday model"
                : reviewStep
                  ? "Choose a shell review model"
                  : "Decide where your agent works"}
            </h2>
            <ErrorNotice error={error} />
            {!storageAvailable && (
              <p role="alert">
                Browser storage is off. Your choices will be lost on refresh.
                Enable storage before saving.
              </p>
            )}
            {phase && <p role="status">{phase}</p>}
            {draft.pending ? (
              <div className={styles.stack}>
                <h3>Resume your saved setup</h3>
                <p>
                  Your settings may already be saved. Resume to check the saved
                  files and first chat. No message will be sent.
                </p>
                <Button
                  loading={busy}
                  onClick={() =>
                    void run(async () => {
                      await reconcile(draft.pending!);
                      await openConversation(draft.pending!);
                    })
                  }
                >
                  Resume setup
                </Button>
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() => {
                    patch({ pending: undefined });
                    void preview.refetch();
                  }}
                >
                  Review changes
                </Button>
              </div>
            ) : (
              <fieldset disabled={busy} className={styles.fields}>
                {draft.step === 0 && (
                  <div className={styles.stack}>
                    <ModelEditor
                      draft={draft.modelDraft}
                      onDraftChange={changeModelDraft}
                      value={selection.model ?? null}
                      onChange={(model) =>
                        changeSelection({
                          model,
                          shell_review_model: null,
                          shell_review: model.authentication.kind !== "api_key",
                        })
                      }
                      onReady={setModelReady}
                    />
                    <details className={styles.advanced}>
                      <summary>Agent options</summary>
                      <div className={styles.stack}>
                        <label className={styles.check}>
                          <input
                            type="checkbox"
                            checked={selection.tool_capabilities == null}
                            onChange={(event) =>
                              changeSelection({
                                tool_capabilities: event.target.checked
                                  ? null
                                  : [],
                              })
                            }
                          />{" "}
                          Enable recommended model tools
                        </label>
                        <label className={styles.check}>
                          <input
                            type="checkbox"
                            checked={
                              selection.include_default_subagents ?? true
                            }
                            onChange={(event) =>
                              changeSelection({
                                include_default_subagents: event.target.checked,
                              })
                            }
                          />{" "}
                          Include built-in subagents
                        </label>
                        <FormField label="Additional agent instructions">
                          <Textarea
                            rows={4}
                            value={selection.instructions ?? ""}
                            onChange={(event) =>
                              changeSelection({
                                instructions: event.target.value,
                              })
                            }
                          />
                        </FormField>
                      </div>
                    </details>
                  </div>
                )}
                {reviewStep && selection.model && (
                  <div className={styles.stack}>
                    <ShellReviewModel
                      model={selection.model}
                      value={selection.shell_review_model ?? ""}
                      onChange={(value) =>
                        changeSelection({
                          shell_review_model: value || null,
                          shell_review: !!value.trim(),
                        })
                      }
                    />
                  </div>
                )}
                {draft.step === workspaceStep && (
                  <div className={styles.stack}>
                    <div
                      className={styles.environments}
                      role="group"
                      aria-label="Local mode · Harness server"
                    >
                      {(
                        [
                          [
                            "environment-native",
                            "Full Control",
                            Desktop,
                            "Run directly as your host account with full filesystem and network access.",
                          ],
                          [
                            "environment-sandbox",
                            "Sandbox",
                            ShieldCheck,
                            "Runs in a sandbox. Downloads envd if needed and checks isolation.",
                          ],
                        ] as const
                      ).map(([value, label, Icon, description]) => (
                        <button
                          type="button"
                          key={value}
                          aria-label={label}
                          aria-pressed={selection.environment_profile === value}
                          onClick={() =>
                            changeSelection({ environment_profile: value })
                          }
                        >
                          <Icon size={22} />
                          <strong>{label}</strong>
                          <span>{description}</span>
                        </button>
                      ))}
                    </div>
                    <div className={styles.runtimeStatus}>
                      <strong>
                        {sandbox ? "Sandbox runtime" : "Direct execution"}
                      </strong>
                      <p>
                        {sandbox
                          ? "envd runs on this server."
                          : "No envd download is needed for Full Control with Direct."}
                      </p>
                      <Link to="/settings/runtime">
                        Manage envd & Computer Use
                      </Link>
                      {sandbox && (
                        <>
                          <p role="status">{preparationStatus}</p>
                          {sandboxCheck.isFetching &&
                            runtime.data?.phase === "downloading" && (
                              <progress
                                aria-label="envd download"
                                max={runtime.data.total_bytes ?? undefined}
                                value={
                                  runtime.data.total_bytes
                                    ? runtime.data.downloaded_bytes
                                    : undefined
                                }
                              />
                            )}
                          {sandboxCheck.isFetching && (
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => {
                                setCancelledTargets(targetKey);
                                void queries.cancelQueries({
                                  queryKey: ["setup-sandbox", scope, targets],
                                  exact: true,
                                });
                              }}
                            >
                              Cancel preparation
                            </Button>
                          )}
                          {Object.entries(readiness)
                            .filter(([, value]) => !value.ready)
                            .map(([path, value]) => (
                              <div key={path}>
                                <p>{value.message}</p>
                                {value.instructions?.map((line) => (
                                  <p key={line}>{line}</p>
                                ))}
                              </div>
                            ))}
                          {!preparing && !sandboxReady && currentPreview && (
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => {
                                if (cancelled) setCancelledTargets(undefined);
                                else void sandboxCheck.refetch();
                              }}
                            >
                              Retry sandbox
                            </Button>
                          )}
                        </>
                      )}
                      <ErrorNotice error={preparationError} />
                      {preview.error && (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => void preview.refetch()}
                        >
                          Check again
                        </Button>
                      )}
                    </div>
                    <TextField
                      label="Project directory (optional)"
                      value={selection.project_path ?? ""}
                      onChange={(path) =>
                        changeSelection({
                          project: path ? "project-workspace" : null,
                          project_path: path || null,
                        })
                      }
                      description="An existing folder on the server. Leave blank for a new workspace."
                    />
                    <LiveOptIn
                      model={selection.model}
                      enabled={selection.enable_live ?? true}
                      onChange={(enable_live) =>
                        changeSelection({ enable_live })
                      }
                    />
                    {!apiKey && (
                      <details className={styles.advanced}>
                        <summary>Shell review</summary>
                        <label className={styles.check}>
                          <input
                            type="checkbox"
                            checked={selection.shell_review ?? true}
                            onChange={(event) =>
                              changeSelection({
                                shell_review: event.target.checked,
                              })
                            }
                          />{" "}
                          Review shell commands before execution
                        </label>
                        <p>Uses model requests to review commands.</p>
                      </details>
                    )}
                    {apiKey && (
                      <p>
                        Shell review:{" "}
                        {selection.shell_review_model ||
                          "Off — no model selected"}
                      </p>
                    )}
                    <div className={styles.summary}>
                      <strong>{selection.model?.route}</strong>
                      <span>
                        {sandbox ? "Sandbox" : "Full Control"} ·{" "}
                        {selection.project_path || "Without a project"}
                      </span>
                    </div>
                    {currentPreview && (
                      <>
                        <p>
                          Automatic memory updates use model quota. Manage them
                          in Settings → Memory.
                        </p>
                        <details className={styles.advanced}>
                          <summary>
                            Generated files (
                            {Object.keys(currentPreview.files).length})
                          </summary>
                          {Object.entries(currentPreview.files).map(
                            ([path, content]) => (
                              <details key={path}>
                                <summary>{path}</summary>
                                <pre>{content}</pre>
                              </details>
                            ),
                          )}
                        </details>
                      </>
                    )}
                  </div>
                )}
              </fieldset>
            )}
          </div>
        </DialogPanel>
        <DialogFooter variant="bare" className={styles.footer}>
          {workspaceActive && (
            <p className={styles.preparationStatus} role="status">
              {phase || preparationStatus}
            </p>
          )}
          <button
            type="button"
            className={styles.later}
            disabled={busy}
            onClick={close}
          >
            Set up later
          </button>
          <div>
            <span className={styles.stepCount}>
              Step {draft.step + 1} of {steps.length}
            </span>
            {draft.step > 0 && !draft.pending && (
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => patch({ step: draft.step - 1 })}
              >
                Back
              </Button>
            )}
            {!draft.pending &&
              (draft.step === workspaceStep ? (
                <Button
                  loading={busy}
                  disabled={
                    busy ||
                    preparing ||
                    !!preparationError ||
                    !currentPreview ||
                    (sandbox && !sandboxReady)
                  }
                  onClick={() => void save()}
                >
                  Save and start <ArrowRight />
                </Button>
              ) : (
                <Button
                  disabled={
                    busy ||
                    (draft.step === 0 && !modelReady) ||
                    (reviewStep &&
                      !!selection.shell_review_model &&
                      /\s/.test(selection.shell_review_model))
                  }
                  onClick={() => patch({ step: draft.step + 1 })}
                >
                  {reviewStep && !selection.shell_review_model
                    ? "Skip shell review"
                    : "Continue"}{" "}
                  <ArrowRight />
                </Button>
              ))}
          </div>
        </DialogFooter>
      </DialogPopup>
    </Dialog>
  );
}

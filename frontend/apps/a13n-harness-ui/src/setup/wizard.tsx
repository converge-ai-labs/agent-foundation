import { LanguageChoice } from "../i18n/language";
import { useTranslation } from "../i18n";
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
  Switch,
} from "a13n-ui";
import { ArrowRight, Check, ShieldCheck, Desktop } from "@phosphor-icons/react";
import { useTransport } from "../transport/context";
import { ApiError, result, type Schema } from "../transport/client";
import { ErrorNotice, TextField } from "../shell/ui";
import { SettingsRow } from "../shell/settings";
import {
  connectionId,
  type ModelEditorDraft,
} from "../configuration/model-editor";
import { importablePresets, SetupConnections } from "./connections";
import {
  dismissWizard,
  finishWizard,
  readWizardDraft,
  saveWizardDraft,
  type WizardDraft,
} from "./wizard-state";
import { ShellReviewModel } from "./shell-review";
import styles from "./wizard.module.css";
type Selection = Schema<"SetupSelection">;

export function SetupWizard({
  status,
  returnTo,
}: {
  status: Schema<"SetupStatus">;
  returnTo?: WizardDraft["returnTo"];
}) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const queries = useQueryClient();
  const navigate = useNavigate();
  const scope = status.draft_scope!;
  const [draft, setDraft] = useState<WizardDraft>(() => {
    const existing = readWizardDraft(scope);
    if (
      existing?.pending ||
      (existing && existing.returnTo === returnTo && existing.track)
    )
      return existing;
    let agentId = "agent-primary";
    for (let i = 2; status.agents[agentId]; i += 1)
      agentId = `agent-primary-${i}`;
    return {
      version: 2,
      step: 0,
      originalDefault: status.default_agent,
      returnTo,
      selection: {
        environment_profile: "environment-native",
        ...status.defaults,
        default_agent: agentId,
        set_as_default: !status.default_agent,
        configure_workspace: !status.default_agent,
        include_default_subagents: true,
      },
      threadId: `thread_${crypto.randomUUID().replaceAll("-", "")}`,
    };
  });
  const [modelReady, setModelReady] = useState(false);
  const [acpNeedsSignIn, setAcpNeedsSignIn] = useState(false);
  const onConnectionReady = useCallback(
    (ready: boolean, needsSignIn = false) => {
      setModelReady(ready);
      setAcpNeedsSignIn(needsSignIn);
    },
    [],
  );
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
  const codex = selection.model?.authentication.kind === "codex_subscription";
  const acp = selection.primary_acp_agent;
  const steps = [t("Start"), t("Connect"), t("Workspace")];
  const workspaceStep = 2;
  const workspaceActive = draft.step === workspaceStep && !draft.pending;
  const configureWorkspace =
    selection.configure_workspace !== false &&
    selection.set_as_default !== false;
  const configureDefaults = selection.set_as_default !== false;
  const needsReviewModel =
    apiKey &&
    configureDefaults &&
    !!selection.shell_review &&
    !selection.shell_review_model;
  const connectionsQuery = useQuery({
    queryKey: ["model-choices"],
    queryFn: ({ signal }) =>
      result(client.GET("/api/models/choices", { signal })),
  });
  const presetsQuery = useQuery({
    queryKey: ["acp-presets"],
    queryFn: ({ signal }) =>
      result(client.GET("/api/acp-agents/presets", { signal })),
  });
  const connections = connectionsQuery.data?.connections ?? [];
  const presets = presetsQuery.data ?? [];
  const detectedEnvKeys = status.detected_env_keys ?? [];
  const detectedAccounts = status.providers
    .filter((provider) => provider.available)
    .map(
      ({ provider }) =>
        connections.find((item) => item.account?.provider === provider)?.account
          ?.label ?? provider,
    );
  const detectedClis = importablePresets(presets, status);
  const extraClis = importablePresets(
    presets,
    status,
    acp,
    selection.model?.authentication.kind,
  );
  const selectEngine = (
    engine: string,
    preset?: NonNullable<Selection["primary_acp_agent"]>["preset"],
  ) => {
    if (engine === draft.engine) return;
    setModelReady(false);
    setAcpNeedsSignIn(false);
    setDraft((value) => ({
      ...value,
      engine,
      modelDraft: undefined,
      selection: {
        ...value.selection,
        model: null,
        primary_acp_agent: preset ? { preset } : null,
        additional_acp_presets: [],
        shell_review_model: null,
        shell_review: false,
        enable_live: false,
        enable_dictation: false,
        auto_organize_memory: !preset,
        configure_workspace:
          !value.originalDefault ||
          !!(
            value.selection.set_as_default &&
            preset &&
            status.environment_profile !== "environment-native"
          ),
        ...(preset || value.originalDefault
          ? {
              environment_profile:
                !preset && status.environment_profile === "environment-sandbox"
                  ? "environment-sandbox"
                  : "environment-native",
            }
          : {}),
      },
    }));
  };
  const changeModel = useCallback((model: NonNullable<Selection["model"]>) => {
    setDraft((value) => {
      const kind = model.authentication.kind;
      const previous = value.selection.model;
      const reviewChanged =
        model.route !== previous?.route ||
        model.model_configuration?.base_url !==
          previous?.model_configuration?.base_url;
      return {
        ...value,
        selection: {
          ...value.selection,
          model,
          ...(kind !== previous?.authentication.kind
            ? {
                enable_live: kind === "codex_subscription",
                enable_dictation: kind === "codex_subscription",
                shell_review:
                  kind === "codex_subscription" || kind === "grok_subscription",
                auto_organize_memory: true,
              }
            : {}),
          ...(reviewChanged && kind === "api_key"
            ? { shell_review_model: null, shell_review: false }
            : {}),
        },
      };
    });
  }, []);
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
  const sandbox =
    configureWorkspace &&
    selection.environment_profile === "environment-sandbox";
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
    ? t("Checking configuration…")
    : sandbox && sandboxCheck.isFetching
      ? runtime.data?.phase === "downloading"
        ? runtime.data.total_bytes
          ? t("Downloading envd… {{downloaded}} MB / {{total}} MB", {
              downloaded: Math.round(
                (runtime.data.downloaded_bytes ?? 0) / 1024 / 1024,
              ),
              total: Math.round(runtime.data.total_bytes / 1024 / 1024),
            })
          : t("Downloading envd… {{downloaded}} MB", {
              downloaded: Math.round(
                (runtime.data.downloaded_bytes ?? 0) / 1024 / 1024,
              ),
            })
        : runtime.data?.phase === "verifying"
          ? t("Verifying envd download…")
          : runtime.data?.phase === "checking"
            ? t("Checking envd installation…")
            : runtime.data?.installed
              ? t("Checking Sandbox isolation…")
              : t("Preparing envd download…")
      : cancelled
        ? t("Preparation cancelled. Retry or choose Full Control.")
        : preparationError
          ? t("Setup needs attention. Review the error above and retry.")
          : sandbox && !sandboxReady
            ? t("Sandbox is not ready. Retry or choose Full Control.")
            : currentPreview
              ? t("Ready to save")
              : t("Checking configuration…");

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
    const agentId = pending.agentId ?? pending.selection.default_agent;
    if (
      saved.diagnostic ||
      !agentId ||
      (pending.selection.set_as_default === false
        ? !saved.agents[agentId]
        : saved.needed || saved.default_agent !== agentId)
    )
      throw new Error(
        "Check the saved configuration files before trying again.",
      );
  };
  const openConversation = async (
    pending: NonNullable<WizardDraft["pending"]>,
  ) => {
    if (draft.returnTo || pending.selection.set_as_default === false) {
      finishWizard(scope);
      void queries.invalidateQueries();
      navigate(draft.returnTo ?? "/settings/agents", { replace: true });
      return;
    }
    const agentId = pending.agentId ?? pending.selection.default_agent;
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
                agent_id: agentId,
                project_id: pending.selection.project ?? null,
                environment_profile_id: pending.selection.environment_profile,
                ...(pending.selection.primary_acp_agent
                  ? { environment_bindings: [] }
                  : {}),
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
      configured.agent_source.kind !==
        (pending.selection.primary_acp_agent ? "acp_agent" : "agent") ||
      configured.agent_source.id !== agentId ||
      (!!pending.selection.primary_acp_agent &&
        !!configured.environment_bindings?.length) ||
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
        needsReviewModel ||
        (sandbox && !sandboxReady)
      )
        return;
      const pending = {
        selection,
        files: currentPreview.files,
        agentId: currentPreview.agent_id ?? undefined,
      };
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
            publication.error_message || t("Setup was only partially saved."),
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
    navigate(draft.returnTo ?? "/?setup=later", { replace: true });
  };
  return (
    <Dialog
      open
      disablePointerDismissal
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <DialogPopup
        initialFocus={heading}
        finalFocus={false}
        className={`${styles.wizard} ${draft.step === 0 && !draft.pending ? styles.start : ""}`}
        closeProps={{
          "aria-label": t("Cancel setup"),
          disabled: busy,
        }}
      >
        <DialogHeader>
          <DialogTitle className={styles.title}>
            {t("Set up Harness")}
          </DialogTitle>
          <DialogDescription>
            {t("Connect a model or local agent and choose where it can work.")}
          </DialogDescription>
          {!draft.returnTo && <LanguageChoice />}
          <ol className={styles.steps} aria-label={t("Setup progress")}>
            {steps.map((step, index) => (
              <li
                key={index}
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
                ? t("Choose how to start")
                : draft.step === 1
                  ? t("Connect your primary engine")
                  : t("Capabilities and workspace")}
            </h2>
            <ErrorNotice
              error={error}
              message={
                error instanceof Error && !(error instanceof ApiError)
                  ? t(error.message)
                  : undefined
              }
            />
            {!storageAvailable && (
              <p role="alert">
                {t(
                  "Browser storage is off. Your choices will be lost on refresh. Enable storage before saving.",
                )}
              </p>
            )}
            {phase && <p role="status">{t(phase)}</p>}
            {draft.pending ? (
              <div className={styles.stack}>
                <h3>{t("Resume your saved setup")}</h3>
                <p>
                  {t(
                    "Your settings may already be saved. Resume to check the saved files and first chat. No message will be sent.",
                  )}
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
                  {t("Resume setup")}
                </Button>
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() => {
                    patch({ pending: undefined });
                    void preview.refetch();
                  }}
                >
                  {t("Review changes")}
                </Button>
              </div>
            ) : (
              <fieldset disabled={busy} className={styles.fields}>
                {draft.step === 0 && (
                  <div
                    className={styles.connections}
                    role="group"
                    aria-label={t("Connection tracks")}
                  >
                    {(["subscription", "api"] as const).map((track) => (
                      <button
                        key={track}
                        type="button"
                        aria-pressed={draft.track === track}
                        onClick={() => {
                          if (draft.track !== track) {
                            patch({ track });
                            selectEngine(
                              track === "subscription"
                                ? "codex"
                                : "openai-responses",
                            );
                          }
                        }}
                      >
                        <strong>
                          {track === "subscription"
                            ? t("Subscription")
                            : t("API Key")}
                        </strong>
                        {track === "subscription" ? (
                          <>
                            <small>{t("Recommended")}</small>
                            <span>
                              {t(
                                "Use a subscription account or a local AI CLI.",
                              )}
                            </span>
                            {!!detectedAccounts.length && (
                              <small>
                                {t("Accounts found: {{names}}", {
                                  names: detectedAccounts.join(", "),
                                })}
                              </small>
                            )}
                            {!!detectedClis.length && (
                              <small>
                                {t("Local CLIs found: {{names}}", {
                                  names: detectedClis
                                    .map(({ name }) => name)
                                    .join(", "),
                                })}
                              </small>
                            )}
                          </>
                        ) : (
                          <>
                            <span>
                              {t("Connect to an API provider with a key.")}
                            </span>
                            {!!detectedEnvKeys.length && (
                              <small>
                                {t("Server keys found: {{names}}", {
                                  names: detectedEnvKeys.join(", "),
                                })}
                              </small>
                            )}
                          </>
                        )}
                      </button>
                    ))}
                  </div>
                )}
                {draft.step === 1 && draft.track && (
                  <>
                    <ErrorNotice
                      error={connectionsQuery.error || presetsQuery.error}
                    />
                    {connectionsQuery.isPending ? (
                      <p role="status">{t("Loading model connections…")}</p>
                    ) : (
                      <SetupConnections
                        track={draft.track}
                        engine={
                          draft.engine ??
                          connectionId(selection.model ?? null, connections)
                        }
                        connections={connections}
                        presets={presets}
                        draft={draft.modelDraft}
                        recipe={selection.model ?? null}
                        candidate={acp}
                        detectedEnvKeys={detectedEnvKeys}
                        onEngine={selectEngine}
                        onCandidate={(primary_acp_agent) =>
                          changeSelection({ primary_acp_agent })
                        }
                        onDraft={changeModelDraft}
                        onModel={changeModel}
                        onReady={onConnectionReady}
                      />
                    )}
                  </>
                )}
                {draft.step === workspaceStep && (
                  <div className={styles.stack}>
                    {draft.originalDefault && (
                      <SetupToggle
                        label={t("Set as default agent")}
                        description={t("Current default: {{agent}}", {
                          agent:
                            status.agents[draft.originalDefault] ??
                            draft.originalDefault,
                        })}
                        checked={configureDefaults}
                        onChange={(set_as_default) =>
                          changeSelection({
                            set_as_default,
                            configure_workspace: !!(
                              set_as_default &&
                              acp &&
                              status.environment_profile !==
                                "environment-native"
                            ),
                          })
                        }
                      />
                    )}
                    {acp ? (
                      <p>
                        {t(
                          "Local CLI agents use Full Control. Add a native model to use Live Voice, Dictation, Sandbox, and automatic memory organization.",
                        )}
                      </p>
                    ) : (
                      <div className={styles.capabilities}>
                        <h3>{t("Auto-configured capabilities")}</h3>
                        <SetupToggle
                          label={
                            configureDefaults
                              ? t("Recommended tools & built-in subagents")
                              : t("Recommended model tools")
                          }
                          checked={selection.tool_capabilities == null}
                          onChange={(enabled) =>
                            changeSelection({
                              tool_capabilities: enabled ? null : [],
                              include_default_subagents: enabled,
                            })
                          }
                        />
                        {configureDefaults ? (
                          <>
                            {codex && (
                              <>
                                <SetupToggle
                                  label={t("Live Voice")}
                                  checked={selection.enable_live ?? true}
                                  onChange={(enable_live) =>
                                    changeSelection({ enable_live })
                                  }
                                />
                                <SetupToggle
                                  label={t("Voice Dictation")}
                                  checked={selection.enable_dictation ?? true}
                                  onChange={(enable_dictation) =>
                                    changeSelection({ enable_dictation })
                                  }
                                />
                              </>
                            )}
                            <SetupToggle
                              label={
                                codex
                                  ? t("Guardian Shell Review")
                                  : t("Shell review")
                              }
                              checked={!!selection.shell_review}
                              onChange={(shell_review) =>
                                changeSelection({
                                  shell_review,
                                  ...(shell_review
                                    ? {}
                                    : { shell_review_model: null }),
                                })
                              }
                            />
                            {apiKey &&
                              selection.shell_review &&
                              selection.model && (
                                <ShellReviewModel
                                  model={selection.model}
                                  value={selection.shell_review_model ?? ""}
                                  onChange={(value) =>
                                    changeSelection({
                                      shell_review_model: value || null,
                                    })
                                  }
                                />
                              )}
                            <SetupToggle
                              label={t("Auto-organize Memory")}
                              description={t(
                                "Automatic memory updates use model quota.",
                              )}
                              checked={selection.auto_organize_memory ?? true}
                              onChange={(auto_organize_memory) =>
                                changeSelection({ auto_organize_memory })
                              }
                            />
                          </>
                        ) : (
                          <p>
                            {t(
                              "Application defaults stay unchanged when adding a connection.",
                            )}
                          </p>
                        )}
                      </div>
                    )}
                    {!!extraClis.length && (
                      <div className={styles.capabilities}>
                        <h3>{t("Also add detected local CLI agents")}</h3>
                        {extraClis.map(({ id, name }) => (
                          <label key={id} className={styles.check}>
                            <input
                              type="checkbox"
                              checked={
                                selection.additional_acp_presets?.includes(
                                  id,
                                ) ?? false
                              }
                              onChange={(event) =>
                                changeSelection({
                                  additional_acp_presets: event.target.checked
                                    ? [
                                        ...(selection.additional_acp_presets ??
                                          []),
                                        id,
                                      ]
                                    : selection.additional_acp_presets?.filter(
                                        (preset) => preset !== id,
                                      ),
                                })
                              }
                            />
                            {name}
                          </label>
                        ))}
                      </div>
                    )}
                    {configureWorkspace && (
                      <>
                        {acp && draft.originalDefault && (
                          <p>
                            {t(
                              "Setting an ACP agent as default changes the execution default to Full Control.",
                            )}
                          </p>
                        )}
                        <div
                          className={styles.environments}
                          role="group"
                          aria-label={t("Local mode - Harness server")}
                        >
                          {(
                            [
                              [
                                "environment-native",
                                t("Full Control"),
                                Desktop,
                                t(
                                  "Run directly as your host account with full filesystem and network access.",
                                ),
                              ],
                              [
                                "environment-sandbox",
                                t("Sandbox"),
                                ShieldCheck,
                                t(
                                  "Runs in a sandbox. Downloads envd if needed and checks isolation.",
                                ),
                              ],
                            ] as const
                          ).map(([value, label, Icon, description]) => (
                            <button
                              type="button"
                              key={value}
                              aria-label={label}
                              aria-pressed={
                                selection.environment_profile === value
                              }
                              disabled={
                                !!acp && value === "environment-sandbox"
                              }
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
                            {sandbox
                              ? t("Sandbox runtime")
                              : t("Direct execution")}
                          </strong>
                          <p>
                            {sandbox
                              ? t("envd runs on this server.")
                              : t(
                                  "No envd download is needed for Full Control with Direct.",
                                )}
                          </p>
                          <Link to="/settings/runtime">
                            {t("Manage envd & Computer Use")}
                          </Link>
                          {sandbox && (
                            <>
                              <p role="status">{preparationStatus}</p>
                              {sandboxCheck.isFetching &&
                                runtime.data?.phase === "downloading" && (
                                  <progress
                                    aria-label={t("envd download")}
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
                                      queryKey: [
                                        "setup-sandbox",
                                        scope,
                                        targets,
                                      ],
                                      exact: true,
                                    });
                                  }}
                                >
                                  {t("Cancel preparation")}
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
                              {!preparing &&
                                !sandboxReady &&
                                currentPreview && (
                                  <Button
                                    variant="outline"
                                    size="sm"
                                    onClick={() => {
                                      if (cancelled)
                                        setCancelledTargets(undefined);
                                      else void sandboxCheck.refetch();
                                    }}
                                  >
                                    {t("Retry sandbox")}
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
                              {t("Check again")}
                            </Button>
                          )}
                        </div>
                        <TextField
                          label={t("Project directory (optional)")}
                          value={selection.project_path ?? ""}
                          onChange={(path) =>
                            changeSelection({
                              project: path ? "project-workspace" : null,
                              project_path: path || null,
                            })
                          }
                          description={t(
                            "An existing folder on the server. Leave blank for a new workspace.",
                          )}
                        />
                      </>
                    )}
                    {!configureWorkspace && (
                      <ErrorNotice error={preparationError} />
                    )}
                    <details className={styles.advanced}>
                      <summary>{t("Advanced")}</summary>
                      <FormField label={t("Additional agent instructions")}>
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
                      {currentPreview && (
                        <details>
                          <summary>
                            {t("Generated files ({{count}})", {
                              count: Object.keys(currentPreview.files).length,
                            })}
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
                      )}
                    </details>
                  </div>
                )}
              </fieldset>
            )}
          </div>
        </DialogPanel>
        <DialogFooter variant="bare" className={styles.footer}>
          {workspaceActive && (
            <p className={styles.preparationStatus} role="status">
              {phase ? t(phase) : preparationStatus}
            </p>
          )}
          <button
            type="button"
            className={styles.later}
            disabled={busy}
            onClick={close}
          >
            {draft.returnTo ? t("Cancel") : t("Set up later")}
          </button>
          <div>
            <span className={styles.stepCount}>
              {t("Step {{step}} of {{total}}", {
                step: draft.step + 1,
                total: steps.length,
              })}
            </span>
            {draft.step > 0 && !draft.pending && (
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => patch({ step: draft.step - 1 })}
              >
                {t("Back")}
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
                    needsReviewModel ||
                    (sandbox && !sandboxReady)
                  }
                  onClick={() => void save()}
                >
                  {draft.returnTo ? t("Save connection") : t("Save and start")}
                  <ArrowRight />
                </Button>
              ) : (
                <Button
                  disabled={
                    busy ||
                    (draft.step === 0 && !draft.track) ||
                    (draft.step === 1 && !modelReady)
                  }
                  onClick={() => patch({ step: draft.step + 1 })}
                >
                  {draft.step === 1 && acp && acpNeedsSignIn
                    ? t("Continue without signing in")
                    : t("Continue")}
                  <ArrowRight />
                </Button>
              ))}
          </div>
        </DialogFooter>
      </DialogPopup>
    </Dialog>
  );
}

function SetupToggle({
  label,
  description,
  checked,
  onChange,
}: {
  label: string;
  description?: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <SettingsRow label={label} description={description} stackOnNarrow>
      <Switch aria-label={label} checked={checked} onCheckedChange={onChange} />
    </SettingsRow>
  );
}

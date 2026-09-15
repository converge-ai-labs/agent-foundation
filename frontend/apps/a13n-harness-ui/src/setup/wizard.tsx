import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  ChoiceField,
  Dialog,
  DialogPopup,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogPanel,
  DialogFooter,
  FormField,
  Input,
  Textarea,
} from "a13n-ui";
import { ArrowRight, Check, ShieldCheck, Desktop } from "@phosphor-icons/react";
import { useTransport } from "../transport/context";
import { ApiError, result, type Schema } from "../transport/client";
import { ErrorNotice, TextField } from "../shell/ui";
import { ProviderAccount } from "./accounts";
import {
  dismissWizard,
  finishWizard,
  readWizardDraft,
  saveWizardDraft,
  type WizardDraft,
} from "./wizard-state";
import styles from "./wizard.module.css";

const steps = ["Connect", "Choose a model", "Workspace"];
type Selection = Schema<"SetupSelection">;

export function SetupWizard({ status }: { status: Schema<"SetupStatus"> }) {
  const { client } = useTransport();
  const queries = useQueryClient();
  const navigate = useNavigate();
  const scope = status.draft_scope!;
  const choices = status.choices!;
  const [draft, setDraft] = useState<WizardDraft>(
    () =>
      readWizardDraft(scope) ?? {
        version: 1,
        step: 0,
        connection:
          status.providers.find((p) => p.available)?.provider ?? "codex",
        selection: { ...choices.defaults, include_default_subagents: true },
        apiProvider: choices.api_providers[0]!.value,
        modelId: choices.api_providers[0]!.models[0]!,
        baseUrl: choices.api_providers[0]!.base_url,
        preset: "",
        credential: "",
        threadId: `thread_${crypto.randomUUID().replaceAll("-", "")}`,
      },
  );
  const [secret, setSecret] = useState("");
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<unknown>();
  const [storageAvailable, setStorageAvailable] = useState(true);
  const [phase, setPhase] = useState("");
  const [busy, setBusy] = useState(false);
  const busyRef = useRef(false);
  const preflightAbort = useRef<AbortController | null>(null);
  useEffect(() => () => preflightAbort.current?.abort(), []);
  const heading = useRef<HTMLHeadingElement>(null);
  const [preview, setPreview] = useState<{
    key: string;
    value: Schema<"SetupPreview">;
  }>();
  const [readiness, setReadiness] = useState<
    Record<string, Schema<"EnvironmentReadiness">>
  >({});
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
  const modelOptions = useQuery({
    queryKey: [
      "setup-model-options",
      draft.apiProvider,
      draft.modelId,
      draft.baseUrl,
    ],
    queryFn: ({ signal }) =>
      result(
        client.POST("/api/setup/model-options", {
          body: {
            provider: draft.apiProvider,
            model_id: draft.modelId,
            base_url: draft.baseUrl,
          },
          signal,
        }),
      ),
    enabled:
      draft.connection === "api_key" && !!draft.modelId && draft.step === 1,
  });
  const keys = useQuery({
    queryKey: ["keys"],
    queryFn: ({ signal }) => result(client.GET("/api/auth/keys", { signal })),
    enabled: draft.connection === "api_key",
  });
  const saveKey = useMutation({
    mutationFn: async () => {
      const reference =
        draft.credential || `key-${crypto.randomUUID().replaceAll("-", "")}`;
      // Allocate once before the request. A lost acknowledgement keeps this reference.
      patch({ credential: reference });
      await result(
        client.PUT("/api/auth/keys", {
          body: { credential_ref: reference, key: secret },
        }),
      );
      return reference;
    },
    onSuccess: () => {
      setSecret("");
      void queries.invalidateQueries({ queryKey: ["keys"] });
    },
    onError: () => {
      void queries.invalidateQueries({ queryKey: ["keys"] });
    },
  });
  const apiReady =
    !!draft.credential &&
    !!keys.data?.some((key) => key.credential_ref === draft.credential);
  const selection: Selection = {
    ...draft.selection,
    providers: draft.connection === "api_key" ? [] : [draft.connection],
    default_agent:
      draft.connection === "api_key"
        ? "agent-api-key"
        : `agent-${draft.connection}`,
    ...(draft.connection === "api_key"
      ? {
          api_key_model: {
            ...draft.selection.api_key_model,
            route: `${draft.apiProvider}:${draft.modelId}`,
            authentication: {
              kind: "api_key",
              credential_ref: draft.credential,
            },
            model_configuration: {
              ...(draft.baseUrl ? { base_url: draft.baseUrl } : {}),
              ...(draft.sessionAffinityHeader
                ? { session_affinity_header: draft.sessionAffinityHeader }
                : {}),
            },
          },
        }
      : { api_key_model: null }),
  };
  const selectionKey = JSON.stringify(selection);
  const currentPreview =
    preview?.key === selectionKey ? preview.value : undefined;
  const sandbox = selection.environment_profile === "environment-sandbox";
  const sandboxReady =
    !!currentPreview?.project_paths.length &&
    currentPreview.project_paths.every((path) => readiness[path]?.ready);
  useEffect(() => {
    setReadiness({});
  }, [
    selection.environment_profile,
    selection.project,
    selection.project_path,
  ]);

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
      preflightAbort.current = null;
    }
  };
  const review = () =>
    run(async () => {
      setPhase("Preparing your configuration…");
      const value = await result(
        client.POST("/api/setup/preview", { body: selection }),
      );
      setPreview({ key: selectionKey, value });
    });
  const checkSandbox = () =>
    run(async () => {
      if (!currentPreview) return;
      setPhase("Checking Sandbox isolation…");
      const abort = new AbortController();
      preflightAbort.current = abort;
      setReadiness({});
      const results: typeof readiness = {};
      for (const path of currentPreview.project_paths) {
        results[path] = await result(
          client.POST("/api/environments/preflight", {
            body: { profile_id: "environment-sandbox", project_path: path },
            signal: abort.signal,
          }),
        );
        setReadiness({ ...results });
      }
    });
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
          "Some files are missing or changed. Review the remaining changes before explicitly saving again; existing resources will be preserved.",
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
        "Configuration needs attention. Inspect the saved files before trying again.",
      );
  };
  const openConversation = async (
    pending: NonNullable<WizardDraft["pending"]>,
  ) => {
    setPhase("Opening your first conversation…");
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
        "The first conversation already exists with different choices. Inspect its Agent, Project, and execution environment before continuing; this setup has not changed that conversation.",
      );
    finishWizard(scope);
    navigate(`/threads/${encodeURIComponent(draft.threadId)}?compose=1`, {
      replace: true,
    });
    void queries.invalidateQueries();
  };
  const save = () =>
    run(async () => {
      if (!currentPreview || (sandbox && !sandboxReady)) return;
      const pending = { selection, files: currentPreview.files };
      const next = { ...draft, pending };
      // Persist the exact publication intent before sending. Reload never resubmits it.
      if (!saveWizardDraft(scope, next))
        throw new Error(
          "Browser storage is unavailable. Allow local storage before saving so an interrupted publication can be recovered safely.",
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
    if (busyRef.current || saveKey.isPending) return;
    dismissWizard(scope);
    navigate("/?setup=later", { replace: true });
  };
  const nextModel = () => {
    if (draft.connection === "api_key") {
      const options = modelOptions.data;
      const preset =
        options?.presets.find((p) => p.value === draft.preset) ??
        options?.presets[0];
      if (!options || !preset) return;
      patch({
        step: 2,
        selection: {
          ...draft.selection,
          api_key_model: {
            route: `${draft.apiProvider}:${draft.modelId}`,
            authentication: {
              kind: "api_key",
              credential_ref: draft.credential,
            },
            settings: preset.settings,
            model_configuration: {
              ...(draft.baseUrl ? { base_url: draft.baseUrl } : {}),
              ...(draft.sessionAffinityHeader
                ? { session_affinity_header: draft.sessionAffinityHeader }
                : {}),
            },
            model_characteristics: {
              context_window_tokens: draft.apiContext ?? options.context_window,
              proactive_context_management_threshold: 0.65,
              compact_threshold: 0.9,
            },
          },
        },
      });
    } else patch({ step: 2 });
  };
  const provider = choices.api_providers.find(
    (p) => p.value === draft.apiProvider,
  )!;
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
          disabled: busy || saveKey.isPending,
        }}
      >
        <DialogHeader>
          <p className={styles.eyebrow}>YOUR FIRST CONVERSATION STARTS HERE</p>
          <DialogTitle>Make this workbench yours</DialogTitle>
          <DialogDescription>
            Connect a model and choose where it can work. No model requests
            until you send a message.
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
              {
                [
                  "Connect your model account",
                  "Choose your everyday model",
                  "Decide where your agent works",
                ][draft.step]
              }
            </h2>
            <ErrorNotice error={error} />
            {!storageAvailable && (
              <p role="alert">
                Browser storage is unavailable. Your choices will not survive a
                refresh; enable storage before saving setup.
              </p>
            )}
            {phase && <p role="status">{phase}</p>}
            {preflightAbort.current && (
              <Button
                variant="outline"
                onClick={() => {
                  preflightAbort.current?.abort();
                  setReadiness({});
                }}
              >
                Cancel readiness check
              </Button>
            )}
            {draft.pending ? (
              <div className={styles.stack}>
                <h3>Resume your saved setup</h3>
                <p>
                  Settings may have been saved even if the response was lost.
                  Check the exact files and first conversation before retrying.
                  No prompt will be sent.
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
                  Check saved setup and open conversation
                </Button>
                <Button
                  variant="outline"
                  disabled={busy}
                  onClick={() => {
                    patch({ pending: undefined });
                    setPreview(undefined);
                  }}
                >
                  Review remaining changes
                </Button>
              </div>
            ) : (
              <fieldset disabled={busy} className={styles.fields}>
                {draft.step === 0 && (
                  <>
                    <div
                      className={styles.connections}
                      role="group"
                      aria-label="Model connection"
                    >
                      {(
                        [
                          ["codex", "Codex", "Use your OpenAI account"],
                          ["grok", "Grok", "Use your Grok account"],
                          ["api_key", "API key", "OpenAI, Anthropic & more"],
                        ] as const
                      ).map(([value, label, description]) => (
                        <button
                          type="button"
                          key={value}
                          aria-label={label}
                          aria-pressed={draft.connection === value}
                          onClick={() => {
                            setConnected(false);
                            setSecret("");
                            patch({ connection: value });
                          }}
                        >
                          <strong>{label}</strong>
                          <span>{description}</span>
                        </button>
                      ))}
                    </div>
                    {draft.connection !== "api_key" ? (
                      <ProviderAccount
                        key={draft.connection}
                        provider={draft.connection}
                        inline
                        onReady={setConnected}
                      />
                    ) : (
                      <div className={styles.stack}>
                        <ChoiceField
                          label="API provider"
                          value={draft.apiProvider}
                          options={choices.api_providers.map((p) => ({
                            value: p.value,
                            label: p.label,
                          }))}
                          onValueChange={(value) => {
                            const next = choices.api_providers.find(
                              (p) => p.value === value,
                            )!;
                            setSecret("");
                            patch({
                              apiProvider: value,
                              modelId: next.models[0]!,
                              baseUrl: next.base_url,
                              sessionAffinityHeader: "",
                              credential: "",
                              preset: "",
                              apiContext: undefined,
                            });
                          }}
                        />
                        {!!keys.data?.length && (
                          <ChoiceField
                            label="Use a saved key"
                            value={draft.credential}
                            options={[
                              { value: "", label: "Save a new API key" },
                              ...keys.data.map((key) => ({
                                value: key.credential_ref,
                                label: key.credential_ref,
                              })),
                            ]}
                            onValueChange={(credential) => {
                              setSecret("");
                              patch({ credential });
                            }}
                          />
                        )}
                        {!apiReady && (
                          <>
                            <FormField
                              label="Provider API key"
                              description="Saved immediately in this server's credential store, not in your browser draft. Cancelling setup does not delete a saved key."
                            >
                              <Input
                                type="password"
                                autoComplete="new-password"
                                value={secret}
                                onChange={(event) =>
                                  setSecret(event.target.value)
                                }
                              />
                            </FormField>
                            <Button
                              variant="outline"
                              loading={saveKey.isPending}
                              disabled={!secret.trim()}
                              onClick={() => saveKey.mutate()}
                            >
                              Save API key
                            </Button>
                          </>
                        )}
                        <ErrorNotice error={saveKey.error || keys.error} />
                        {apiReady && (
                          <p role="status">
                            Key saved on this server. Model access has not been
                            tested.
                          </p>
                        )}
                        <p>
                          Credentials belong to this shared server, not your
                          browser identity.
                        </p>
                      </div>
                    )}
                  </>
                )}
                {draft.step === 1 && (
                  <div className={styles.stack}>
                    {draft.connection === "api_key" ? (
                      <>
                        <TextField
                          label="Model ID"
                          value={draft.modelId}
                          onChange={(modelId) =>
                            patch({
                              modelId,
                              preset: "",
                              apiContext: undefined,
                            })
                          }
                          description="Choose a release suggestion or enter your provider's model ID."
                        />
                        <div className={styles.suggestions}>
                          {provider.models.map((id) => (
                            <Button
                              key={id}
                              variant="outline"
                              size="sm"
                              onClick={() =>
                                patch({
                                  modelId: id,
                                  preset: "",
                                  apiContext: undefined,
                                })
                              }
                            >
                              {id}
                            </Button>
                          ))}
                        </div>
                        {provider.base_url && (
                          <TextField
                            label="API base URL"
                            value={draft.baseUrl}
                            onChange={(baseUrl) => patch({ baseUrl })}
                          />
                        )}
                        {provider.supports_session_affinity && (
                          <>
                            <ChoiceField
                              label="Gateway session affinity"
                              value={
                                choices.session_affinity_presets?.some(
                                  (p) =>
                                    p.header === draft.sessionAffinityHeader,
                                )
                                  ? draft.sessionAffinityHeader!
                                  : draft.sessionAffinityHeader
                                    ? "custom"
                                    : "off"
                              }
                              description={
                                choices.session_affinity_presets?.find(
                                  (p) =>
                                    p.header === draft.sessionAffinityHeader,
                                )?.description
                              }
                              options={[
                                { value: "off", label: "Disabled (default)" },
                                ...(choices.session_affinity_presets ?? []).map(
                                  (p) => ({
                                    value: p.header,
                                    label: `${p.label} · ${p.header}`,
                                  }),
                                ),
                                ...(draft.sessionAffinityHeader &&
                                !choices.session_affinity_presets?.some(
                                  (p) =>
                                    p.header === draft.sessionAffinityHeader,
                                )
                                  ? [
                                      {
                                        value: "custom",
                                        label: "Custom header",
                                      },
                                    ]
                                  : []),
                              ]}
                              onValueChange={(value) => {
                                if (value !== "custom")
                                  patch({
                                    sessionAffinityHeader:
                                      value === "off" ? "" : value,
                                  });
                              }}
                            />
                            <FormField
                              label="Session affinity header"
                              description="Choose a preset above or type any custom header name. Its value is a stable UUID derived from the current Thread ID. Leave empty to disable. Configure your gateway to route by this header; sending it alone does not guarantee provider pinning."
                            >
                              <Input
                                name="session_affinity_header"
                                autoComplete="off"
                                maxLength={128}
                                placeholder="e.g. x-conversation-id"
                                value={draft.sessionAffinityHeader ?? ""}
                                onChange={(event) =>
                                  patch({
                                    sessionAffinityHeader: event.target.value,
                                  })
                                }
                              />
                            </FormField>
                          </>
                        )}
                        <ErrorNotice error={modelOptions.error} />
                        {modelOptions.data && (
                          <ChoiceField
                            label="Reasoning & output budget"
                            value={
                              draft.preset ||
                              modelOptions.data.presets[0]?.value ||
                              ""
                            }
                            options={modelOptions.data.presets}
                            onValueChange={(preset) => patch({ preset })}
                          />
                        )}
                      </>
                    ) : (
                      <>
                        <ChoiceField
                          label="Model"
                          value={String(
                            draft.selection[
                              draft.connection === "codex"
                                ? "codex_model"
                                : "grok_model"
                            ],
                          )}
                          options={
                            choices.subscription_models[draft.connection]!
                          }
                          onValueChange={(value) =>
                            changeSelection(
                              draft.connection === "codex"
                                ? {
                                    codex_model:
                                      value as Selection["codex_model"],
                                  }
                                : {
                                    grok_model:
                                      value as Selection["grok_model"],
                                  },
                            )
                          }
                        />
                        <p>
                          Release-reviewed defaults for coding, reasoning, and
                          context management. Availability depends on your
                          account.
                        </p>
                      </>
                    )}
                    <details className={styles.advanced}>
                      <summary>Advanced model & agent options</summary>
                      <div className={styles.stack}>
                        {draft.connection === "codex" && (
                          <>
                            <ChoiceField
                              label="Reasoning effort"
                              value={draft.selection.codex_thinking!}
                              options={["low", "medium", "high", "xhigh"].map(
                                (value) => ({ value, label: value }),
                              )}
                              onValueChange={(value) =>
                                changeSelection({
                                  codex_thinking:
                                    value as Selection["codex_thinking"],
                                })
                              }
                            />
                            <TextField
                              label="Working context budget"
                              value={String(
                                draft.selection.codex_context_window,
                              )}
                              type="number"
                              onChange={(value) =>
                                changeSelection({
                                  codex_context_window: Number(value),
                                })
                              }
                            />
                          </>
                        )}
                        {draft.connection === "api_key" &&
                          modelOptions.data && (
                            <TextField
                              label="Working context budget"
                              type="number"
                              value={String(
                                draft.apiContext ??
                                  modelOptions.data.context_window,
                              )}
                              onChange={(value) =>
                                patch({ apiContext: Number(value) })
                              }
                              description={
                                modelOptions.data.known_context_window
                                  ? `Bundled context limit: ${modelOptions.data.known_context_window.toLocaleString()} tokens.`
                                  : "Unknown model limit. This is an editable working budget, not a provider guarantee."
                              }
                            />
                          )}
                        <p>
                          Recommended native tools are selected by the server
                          for this connection. Their provider usage charges may
                          apply.
                        </p>
                        <label>
                          <input
                            type="checkbox"
                            checked={draft.selection.tool_capabilities == null}
                            onChange={(event) =>
                              changeSelection({
                                tool_capabilities: event.target.checked
                                  ? null
                                  : [],
                              })
                            }
                          />{" "}
                          Enable recommended native tools
                        </label>
                        <label>
                          <input
                            type="checkbox"
                            checked={
                              draft.selection.include_default_subagents ?? true
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
                            value={draft.selection.instructions ?? ""}
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
                {draft.step === 2 && (
                  <div className={styles.stack}>
                    <div
                      className={styles.environments}
                      role="group"
                      aria-label="Execution environment"
                    >
                      {(
                        [
                          [
                            "environment-native",
                            "Full Control",
                            Desktop,
                            "Runs as the server's Host account. This is not a sandbox.",
                          ],
                          [
                            "environment-sandbox",
                            "Sandbox",
                            ShieldCheck,
                            "Isolated execution. A readiness check is required before saving.",
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
                    <TextField
                      label="Project directory (optional)"
                      value={selection.project_path ?? ""}
                      onChange={(path) =>
                        changeSelection({
                          project: path ? "project-workspace" : null,
                          project_path: path || null,
                        })
                      }
                      description="Existing directory on the server. Leave blank to start without a Project; the conversation gets its own working directory."
                    />
                    <details className={styles.advanced}>
                      <summary>Shell review</summary>
                      <label>
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
                      <p>
                        Review is not isolation and uses model requests when you
                        work. Existing root settings stay unchanged.
                      </p>
                    </details>
                    <div className={styles.summary}>
                      <strong>
                        {draft.connection === "api_key"
                          ? draft.modelId
                          : selection[
                              draft.connection === "codex"
                                ? "codex_model"
                                : "grok_model"
                            ]}
                      </strong>
                      <span>
                        {sandbox ? "Sandbox" : "Full Control"} ·{" "}
                        {selection.project_path || "Without a Project"}
                      </span>
                      <p>
                        Account connected does not mean model-request verified.
                        Your first message is the first model request.
                      </p>
                    </div>
                    <Button
                      variant="outline"
                      loading={busy && !phase.includes("Sandbox")}
                      onClick={() => void review()}
                    >
                      Review configuration
                    </Button>
                    {currentPreview && (
                      <>
                        {sandbox && (
                          <>
                            <Button
                              variant="outline"
                              loading={busy}
                              onClick={() => void checkSandbox()}
                            >
                              Check Sandbox readiness
                            </Button>
                            {Object.entries(readiness).map(([path, value]) => (
                              <div key={path} role="status">
                                <strong>
                                  {value.ready
                                    ? "Environment ready"
                                    : "Sandbox needs attention"}
                                </strong>
                                <p>{path}</p>
                                <p>{value.message}</p>
                                {value.instructions?.map((line) => (
                                  <p key={line}>{line}</p>
                                ))}
                              </div>
                            ))}
                          </>
                        )}
                        <details className={styles.advanced}>
                          <summary>
                            Generated files (
                            {Object.keys(currentPreview.files).length})
                          </summary>
                          <p>
                            These are ordinary editable configuration files.
                            Existing resources are preserved.
                          </p>
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
          <Button
            variant="ghost"
            disabled={busy || saveKey.isPending}
            onClick={close}
          >
            Set up later
          </Button>
          <div>
            <span className={styles.stepCount}>Step {draft.step + 1} of 3</span>
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
              (draft.step === 2 ? (
                <Button
                  loading={busy}
                  disabled={!currentPreview || (sandbox && !sandboxReady)}
                  onClick={() => void save()}
                >
                  Save and start chatting <ArrowRight />
                </Button>
              ) : (
                <Button
                  disabled={
                    busy ||
                    saveKey.isPending ||
                    (draft.step === 0
                      ? !(draft.connection === "api_key" ? apiReady : connected)
                      : draft.connection === "api_key" &&
                        (!modelOptions.data ||
                          modelOptions.isFetching ||
                          !!modelOptions.error))
                  }
                  onClick={() =>
                    draft.step === 0 ? patch({ step: 1 }) : nextModel()
                  }
                >
                  Continue <ArrowRight />
                </Button>
              ))}
          </div>
        </DialogFooter>
      </DialogPopup>
    </Dialog>
  );
}

import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
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
import styles from "./wizard.module.css";

const steps = ["Model", "Workspace"];
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
  const selection: Selection = draft.selection;
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
          <DialogTitle>Make this workbench yours</DialogTitle>
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
              {
                ["Choose your everyday model", "Decide where your agent works"][
                  draft.step
                ]
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
                  <div className={styles.stack}>
                    <ModelEditor
                      draft={draft.modelDraft}
                      onDraftChange={changeModelDraft}
                      value={selection.model ?? null}
                      onChange={(model) => changeSelection({ model })}
                      onReady={setModelReady}
                    />
                    <details className={styles.advanced}>
                      <summary>Agent options</summary>
                      <div className={styles.stack}>
                        <label>
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
                          Enable recommended native tools on the new Agent
                        </label>
                        <label>
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
                {draft.step === 1 && (
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
                      <strong>{selection.model?.route}</strong>
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
                        <p>
                          File memory and automatic organization are enabled by
                          default. Organization automatically uses the global
                          default Agent's model; no separate model selection is
                          needed. WebUI may make additional background requests
                          when memory files change. This can consume quota or
                          incur costs. Existing choices are preserved; change
                          these settings under General settings → Memory.
                        </p>
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
          <Button variant="ghost" disabled={busy} onClick={close}>
            Set up later
          </Button>
          <div>
            <span className={styles.stepCount}>Step {draft.step + 1} of 2</span>
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
              (draft.step === 1 ? (
                <Button
                  loading={busy}
                  disabled={!currentPreview || (sandbox && !sandboxReady)}
                  onClick={() => void save()}
                >
                  Save and start chatting <ArrowRight />
                </Button>
              ) : (
                <Button
                  disabled={busy || !modelReady}
                  onClick={() => patch({ step: 1 })}
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

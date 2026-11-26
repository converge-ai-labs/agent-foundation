import {
  AcpOptionFields,
  acpModelOption,
  acpOptionLabel,
  acpRunBlock,
} from "./acp-options";
import { useComposerRunChoices } from "./composer-run-choices";
import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Link, useLocation, useNavigate, useSearchParams } from "react-router";
import { useTranslation } from "../i18n";
import { errorMessage } from "../i18n/error-message";
import { composerMessage } from "./composer-messages";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Folder } from "@phosphor-icons/react";
import { Button, SearchPicker } from "a13n-ui";
import {
  useProjects,
  useSelectors,
  useSetup,
  useSources,
  useTransport,
} from "../transport/context";
import {
  ApiError,
  result,
  type Schema,
  type Transport,
} from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import type { Profile } from "../shell/presence";
import {
  Composer,
  ComposerDrafts,
  useDraft,
  type ComposerBlock,
} from "./composer";
import { NewDraftStore, type NewDraft } from "./new-draft";
import { attachmentSelections, isReadyAttachment } from "./inline-attachments";
import { RunEnvironments, ThreadRunChoices } from "./thread-run-choices";
import { refreshThreadLists } from "./queries";
import { conversationTitle } from "./local-input";
import { ConversationTranscript } from "./transcript";
import { ConversationOpening, useInitialReady } from "./opening";
import styles from "./new-conversation.module.css";

export const NewConversationDrafts = createContext(new NewDraftStore());

export function newConversationPath(
  projectId: string | null = null,
  coordinatorThreadId?: string,
) {
  return `/new?project=${encodeURIComponent(projectId ?? "")}${coordinatorThreadId ? `&coordinator=${encodeURIComponent(coordinatorThreadId)}` : ""}`;
}

async function checkConversation(
  transport: Transport,
  threadId: string,
  draft: NewDraft,
  signal?: AbortSignal,
) {
  try {
    signal?.throwIfAborted();
    await result(
      transport.client.GET("/api/threads/{thread_id}", {
        params: { path: { thread_id: threadId } },
        signal,
      }),
    );
    signal?.throwIfAborted();
    draft.created = true;
    return true;
  } catch (error) {
    signal?.throwIfAborted();
    if (!(error instanceof ApiError) || error.code !== "thread_missing")
      throw error;
    draft.attempted = false;
    return false;
  } finally {
    draft.save();
  }
}

// Creation and admission remain separate. Resolve a lost creation acknowledgement
// by its retained identity, never by allocating another Thread or replaying Send.
export async function ensureConversation(
  transport: Transport,
  threadId: string,
  draft: NewDraft,
  coordinator: boolean,
) {
  if (draft.pending) return draft.pending;
  if (draft.created) return;
  const create = async () => {
    if (draft.attempted) {
      const found = await checkConversation(transport, threadId, draft);
      throw new Error(
        found
          ? "This chat already exists. Open it and check its settings before sending your saved input."
          : "Chat not found. Your input is saved here. Send again to retry creating the same chat.",
      );
    }
    draft.attempted = true;
    draft.composer.coordinator = !draft.coordinatorThreadId && coordinator;
    draft.save();
    try {
      const created = await result(
        transport.client.POST("/api/threads", {
          body: {
            thread_id: threadId,
            defaults: draft.defaults,
            ...(draft.acp ? { acp_options: draft.acp.selection } : {}),
            ...(draft.coordinatorThreadId
              ? { coordinator_thread_id: draft.coordinatorThreadId }
              : coordinator
                ? { coordinator: true }
                : {}),
          },
        }),
      );
      if (created.thread_id !== threadId)
        throw new Error(
          "Couldn't confirm the chat was created. Send again to check.",
        );
      draft.created = true;
    } catch (error) {
      if (
        error instanceof ApiError &&
        error.status >= 400 &&
        error.status < 500 &&
        error.status !== 409
      )
        draft.attempted = false;
      throw error;
    }
  };
  draft.pending = create();
  try {
    await draft.pending;
  } finally {
    draft.pending = undefined;
    draft.save();
  }
}

export function NewConversationPage(props: {
  profile: Profile;
  unauthorized: () => void;
  coordinatorByDefault?: boolean;
}) {
  const drafts = useContext(NewConversationDrafts);
  const composers = useContext(ComposerDrafts);
  const { key } = useLocation();
  // A new navigation can leave a retired composer behind after a successful
  // Send whose saved-page read failed; ordinary rerenders must not replace it.
  const draft = useMemo(
    () => drafts.get(composers, props.coordinatorByDefault),
    [drafts, composers, key, props.coordinatorByDefault],
  );
  return <NewConversation key={draft.threadId} draft={draft} {...props} />;
}

function NewConversation({
  draft,
  profile,
  unauthorized,
  coordinatorByDefault = false,
}: {
  draft: NewDraft;
  coordinatorByDefault?: boolean;
  profile: Profile;
  unauthorized: () => void;
}) {
  const { threadId } = draft;
  const { t } = useTranslation();
  const active = useRef(true);
  const reviewController = useRef<AbortController | null>(null);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
      reviewController.current?.abort();
    };
  }, []);
  const transport = useTransport();
  const queries = useQueryClient();
  const selectors = useSelectors();
  const projects = useProjects();
  const setup = useSetup();
  const navigate = useNavigate();
  const drafts = useContext(NewConversationDrafts);
  const [defaults, setDefaults] = useState(draft.defaults);
  const [ownerId, setOwnerId] = useState(draft.coordinatorThreadId);
  const owner = useQuery({
    queryKey: ["thread", ownerId, "detail"],
    enabled: !!ownerId,
    queryFn: ({ signal }) =>
      result(
        transport.client.GET("/api/threads/{thread_id}", {
          params: { path: { thread_id: ownerId! } },
          signal,
        }),
      ),
  });
  const [preparing, setPreparing] = useState(false);
  const composerDraft = useDraft(threadId);
  const preview = useQuery({
    queryKey: ["new-thread-preview", defaults],
    queryFn: ({ signal }) =>
      result(
        transport.client.POST("/api/threads/configuration-preview", {
          body: defaults,
          signal,
        }),
      ),
  });
  const create = useMutation({
    mutationFn: (coordinator: boolean) =>
      ensureConversation(transport, threadId, draft, coordinator),
    onSettled: (_data, error) => {
      if (draft.created) void refreshThreadLists(queries);
      if (draft.created && error && active.current) openConversation();
    },
  });
  const review = useMutation({
    mutationFn: async () => {
      const controller = new AbortController();
      reviewController.current = controller;
      // A creation started before navigating away still owns this identity.
      // Wait for it instead of reading absence while its POST is in flight.
      if (draft.pending) await draft.pending;
      controller.signal.throwIfAborted();
      return (
        draft.created ||
        checkConversation(transport, threadId, draft, controller.signal)
      );
    },
    onSuccess: (found) => {
      if (found) {
        void refreshThreadLists(queries);
        openConversation();
      }
    },
  });
  const unknownSubmission = composerDraft.submission.kind === "unknown";
  const choicesDisabled =
    preparing || review.isPending || draft.attempted || unknownSubmission;
  // A completed handover navigates away on its own. Waiting briefly keeps a
  // recovery notice from appearing for the frames in between.
  const showRecovery = useSettled(choicesDisabled && !preparing);
  const [search, setSearch] = useSearchParams();
  const requestedProject = search.get("project");
  const requestedOwner = search.get("coordinator");
  useEffect(() => {
    // Home resumes the slot; explicit creation links select Project and ownership.
    if (
      (requestedProject === null && requestedOwner === null) ||
      choicesDisabled
    ) {
      if (
        requestedProject !== (draft.defaults.project_id ?? "") ||
        (requestedOwner || undefined) !== draft.coordinatorThreadId
      )
        setSearch(
          (current) => {
            const next = new URLSearchParams(current);
            next.set("project", draft.defaults.project_id ?? "");
            if (draft.coordinatorThreadId)
              next.set("coordinator", draft.coordinatorThreadId);
            else next.delete("coordinator");
            return next;
          },
          { replace: true },
        );
      return;
    }
    const nextOwner = requestedOwner || undefined;
    if (draft.coordinatorThreadId !== nextOwner) {
      draft.coordinatorThreadId = nextOwner;
      setOwnerId(nextOwner);
      if (nextOwner) composerDraft.coordinator = false;
      composerDraft.notify();
      draft.save();
    }
    if ((draft.defaults.project_id ?? null) === (requestedProject || null))
      return;
    draft.defaults = {
      ...draft.defaults,
      project_id: requestedProject || null,
    };
    setDefaults(draft.defaults);
    draft.save();
  }, [
    requestedProject,
    requestedOwner,
    choicesDisabled,
    draft,
    composerDraft,
    setSearch,
  ]);
  const change = (patch: Schema<"NewThreadDefaults">) => {
    if (choicesDisabled) return;
    draft.defaults = { ...defaults, ...patch };
    setDefaults(draft.defaults);
    draft.save();
    if ("project_id" in patch)
      setSearch(
        (current) => {
          const next = new URLSearchParams(current);
          next.set("project", patch.project_id ?? "");
          return next;
        },
        { replace: true },
      );
  };
  const project = projects.data?.find(
    (item) => item.project_id === defaults.project_id,
  );
  const effective = preview.data?.configuration;
  const canCoordinate = !!effective?.project_id && !preview.error;
  const effectiveAgent = selectors.data?.agents.find(
    (item) => item.agent_id === effective?.agent_source.id,
  );
  const acpAgent =
    effective?.agent_source.kind === "acp_agent"
      ? effective.agent_source.id
      : undefined;
  const acpEnabled = !!acpAgent && !draft.created;
  const acpProject = effective?.project_id ?? undefined;
  const acpKey = JSON.stringify([acpAgent, acpProject]);
  const activeAcpKey = useRef(acpKey);
  activeAcpKey.current = acpKey;
  const acpParams = {
    path: { agent_id: acpAgent ?? "" },
    query: { project_id: acpProject },
  };
  const acpQueryKey = ["new-acp-options", draft.threadId, acpKey] as const;
  const acp = useQuery<Schema<"AcpOptionsPreview">>({
    queryKey: acpQueryKey,
    enabled: acpEnabled,
    retry: false,
    refetchOnWindowFocus: false,
    staleTime: ({ state }) =>
      acpModelOption(state.data?.options) ? Infinity : 0,
    queryFn: async ({ signal }) => {
      const base = await result(
        transport.client.GET("/api/acp-agents/{agent_id}/options", {
          params: acpParams,
          signal,
        }),
      );
      const saved = draft.acp?.key === acpKey ? draft.acp.selection : undefined;
      if (
        saved?.scope === base.scope &&
        Object.keys(saved.values ?? {}).length
      ) {
        try {
          return await result(
            transport.client.POST("/api/acp-agents/{agent_id}/options", {
              params: acpParams,
              signal,
              body: { selection: saved },
            }),
          );
        } catch (error) {
          if (
            !(error instanceof ApiError) ||
            error.code !== "acp_option_invalid"
          )
            throw error;
          if (
            !acpModelOption(base.options) &&
            Object.keys(saved.values ?? {}).some(
              (key) => !base.options.some((option) => option.id === key),
            )
          )
            return base;
        }
      }
      if (
        saved &&
        activeAcpKey.current === acpKey &&
        draft.acp?.selection === saved
      ) {
        draft.acp = undefined;
        draft.save();
      }
      return base;
    },
  });
  const acpChange = useMutation({
    mutationFn: ({
      change,
      data,
      params,
    }: {
      change: Schema<"AcpOptionChange">;
      data: Schema<"AcpOptionsPreview">;
      key: string;
      queryKey: typeof acpQueryKey;
      params: typeof acpParams;
    }) =>
      result(
        transport.client.POST("/api/acp-agents/{agent_id}/options", {
          params,
          body: {
            selection: { scope: data.scope, values: data.selections },
            change,
          },
        }),
      ),
    onSuccess: async (updated, vars) => {
      await queries.cancelQueries({ queryKey: vars.queryKey, exact: true });
      if (activeAcpKey.current !== vars.key) return;
      draft.acp = {
        key: vars.key,
        selection: { scope: updated.scope, values: updated.selections },
      };
      draft.save();
      queries.setQueryData(vars.queryKey, updated);
    },
  });
  const resetAcpChange = acpChange.reset;
  useEffect(() => {
    resetAcpChange();
  }, [acpKey, draft.created, resetAcpChange]);
  const acpChangeMatches = acpChange.variables?.key === acpKey;
  const acpUpdating = acpEnabled && acpChange.isPending && acpChangeMatches;
  const acpUpdateError =
    acpEnabled && acpChangeMatches ? acpChange.error : null;
  const sources = useSources(acpEnabled);
  const acpSource =
    acpAgent &&
    sources.data?.sources.find(
      (item) =>
        item.resource_kind === "acp_agent" &&
        item.resource_ids.includes(acpAgent),
    );
  const acpAuthPath = acpSource
    ? `/settings/source?${new URLSearchParams({ path: acpSource.relative_path })}`
    : undefined;
  const runChoices = useComposerRunChoices(
    composerDraft,
    defaults.agent_id ?? effective?.agent_source.id,
    effective?.default_model_id ??
      (selectors.data ? (effectiveAgent?.model_id ?? null) : undefined),
  );
  const effectiveEnvironment = selectors.data?.environments.find(
    (item) => item.profile_id === effective?.environment_profile_id,
  );
  const ownerAvailable =
    !ownerId ||
    (!owner.isError &&
      !!owner.data &&
      owner.data.thread.role === "coordinator" &&
      !owner.data.thread.archived &&
      owner.data.thread.configuration.project_id === defaults.project_id &&
      !!project);
  const clearOwner = () => {
    if (choicesDisabled) return;
    draft.coordinatorThreadId = undefined;
    setOwnerId(undefined);
    composerDraft.coordinator = false;
    composerDraft.notify();
    draft.save();
    setSearch(
      (current) => {
        const next = new URLSearchParams(current);
        next.delete("coordinator");
        return next;
      },
      { replace: true },
    );
  };
  const acpBlock = acpEnabled
    ? acpRunBlock({
        agentName: effectiveAgent?.name ?? acpAgent,
        hasData: acp.data !== undefined,
        isFetching: acp.isFetching,
        isError: acp.isError,
        error: acp.error,
        onRetryLoad: () => void acp.refetch(),
        updating: acpUpdating,
        updateError: acpUpdateError,
        onRetryUpdate:
          acpChangeMatches && acpChange.variables
            ? () => acpChange.mutate(acpChange.variables!)
            : undefined,
        onDismissUpdateError: () => acpChange.reset(),
        acpAuthPath,
        t,
      })
    : null;
  const runBlock: ComposerBlock | null = review.isPending
    ? {
        kind: "loading",
        message: t("Checking the previous creation attempt…"),
      }
    : ownerId && (owner.isPending || projects.isPending)
      ? {
          kind: "loading",
          message: t("Checking coordinator status…"),
        }
      : ownerId && !ownerAvailable
        ? {
            kind: "action_required",
            message: t(
              "The coordinator must be available in this project. Retry or remove the assignment.",
            ),
            showWhenEmpty: !owner.error,
            action: {
              label: t("Retry"),
              onClick: () => {
                void owner.refetch();
                void projects.refetch();
              },
            },
            secondaryAction: !draft.created
              ? { label: t("Remove assignment"), onClick: clearOwner }
              : undefined,
          }
        : !preview.data && (preview.isFetching || !preview.isError)
          ? {
              kind: "loading",
              message: t("Updating chat settings…"),
            }
          : preview.isError || !preview.data
            ? {
                kind: "error",
                message: t("Review chat settings before sending."),
                showWhenEmpty: false,
              }
            : acpBlock
              ? acpBlock
              : runChoices.pending
                ? selectors.isError
                  ? {
                      kind: "error",
                      message: t("Could not load model settings."),
                      detail: errorMessage(selectors.error, t),
                      showWhenEmpty: true,
                      action: {
                        label: t("Retry"),
                        onClick: () => void selectors.refetch(),
                      },
                    }
                  : {
                      kind: "loading",
                      message: t("Loading model settings…"),
                    }
                : null;
  const [showAvailable, setShowAvailable] = useState(false);
  const projectAligned =
    requestedProject === null ||
    draft.attempted ||
    (requestedProject || null) === (defaults.project_id ?? null);
  const pageReady = useInitialReady(
    showAvailable ||
      (projectAligned &&
        !selectors.isPending &&
        !projects.isPending &&
        !preview.isPending),
  );
  // A home page that is handing its first message over to the chat shows that
  // message where the greeting and Project choice were, so the composer barely
  // moves before the chat opens.
  const handingOver = composerDraft.localInputs.some(
    (input) => input.state !== "rejected" && input.state !== "unknown",
  );
  const openConversation = () => {
    // Admission and page observation are independent. Open the retained identity
    // immediately. Confirmed local input keeps the destination readable while
    // detail, history, focused output and the shared editor initialize.
    if (!active.current) return;
    navigate(`/threads/${encodeURIComponent(threadId)}?compose=1`, {
      replace: true,
    });
  };
  return (
    <ConversationOpening
      ready={pageReady}
      label={t("Preparing your chat…")}
      onContinue={() => setShowAvailable(true)}
    >
      <section className={styles.page} aria-label={t("New chat")}>
        <div
          className={styles.inputArea}
          data-handover={handingOver || undefined}
        >
          <h1 className={styles.welcome}>{t("What can I help with?")}</h1>
          <ConversationTranscript
            entries={[]}
            blocks={[]}
            localInputs={composerDraft.localInputs}
            threadId={threadId}
          />
          <fieldset className={styles.context} disabled={choicesDisabled}>
            <legend className={styles.srOnly}>{t("Chat settings")}</legend>
            <div className={styles.location}>
              <Folder aria-hidden="true" />
              <SearchPicker
                label={t("Project")}
                popupClassName={styles.choicePopup}
                placeholder={t("Without a project")}
                emptyMessage={t("No projects found.")}
                disabled={choicesDisabled || !!ownerId}
                value={defaults.project_id ?? ""}
                onValueChange={(value) => change({ project_id: value || null })}
                groups={[
                  {
                    label: t("Projects"),
                    options: [
                      { value: "", label: t("Without a project") },
                      ...(projects.data ?? []).map((item) => ({
                        value: item.project_id,
                        label: item.name,
                      })),
                      ...(defaults.project_id && !project && !projects.isPending
                        ? [
                            {
                              value: defaults.project_id,
                              label: t("{{id}} (unavailable)", {
                                id: defaults.project_id,
                              }),
                              disabled: true,
                            },
                          ]
                        : []),
                    ],
                  },
                ]}
              />
            </div>
          </fieldset>
          {showRecovery && (
            <div className={styles.projectNotice}>
              <div role="status">
                <strong>{t("Project changes are paused")}</strong>
                <p>
                  {unknownSubmission
                    ? t(
                        "Open the chat to check delivery before changing projects.",
                      )
                    : draft.created
                      ? t(
                          "This draft belongs to an existing chat. Open it to continue.",
                        )
                      : t(
                          "Couldn't confirm the chat was created. Check its status before changing projects. Your input is saved here.",
                        )}
                </p>
              </div>
              {draft.created || unknownSubmission ? (
                <Link to={`/threads/${encodeURIComponent(threadId)}?compose=1`}>
                  {t("Open saved draft")}
                </Link>
              ) : (
                <Button
                  variant="outline"
                  size="sm"
                  disabled={review.isPending}
                  onClick={() => review.mutate()}
                >
                  {review.isPending
                    ? t("Checking chat…")
                    : t("Check chat status")}
                </Button>
              )}
              <ErrorNotice error={review.error} />
            </div>
          )}
          {!choicesDisabled && review.isSuccess && !review.data && (
            <p className={styles.recoveryStatus} role="status">
              {t(
                "No chat found. You can change projects now. Your input is saved here.",
              )}
            </p>
          )}
          <Composer
            dictation={
              selectors.data?.dictation
                ? { words: selectors.data.dictation.context_words ?? 2000 }
                : undefined
            }
            autoFocus={pageReady}
            threadId={threadId}
            activity={{ state: "inactive" }}
            owner={
              ownerId
                ? {
                    title: owner.data
                      ? conversationTitle(owner.data.thread)
                      : ownerId,
                    onRemove: !draft.created ? clearOwner : undefined,
                    locked: choicesDisabled,
                  }
                : undefined
            }
            coordinator={
              ownerId
                ? undefined
                : {
                    active: draft.created && composerDraft.coordinator === true,
                    available: canCoordinate,
                    unavailableReason: !effective?.project_id
                      ? "no_project"
                      : undefined,
                    defaultEnabled:
                      canCoordinate &&
                      (draft.retainedCoordinatorDefault ??
                        coordinatorByDefault),
                    locked: draft.attempted,
                  }
            }
            runBlock={runBlock}
            profile={profile}
            unauthorized={unauthorized}
            reconcile={() => {
              void refreshThreadLists(queries);
            }}
            local={!draft.created}
            acpCommands={acpAgent ? acp.data?.commands : undefined}
            externalAgent={!!acpAgent}
            skillDefaults={defaults}
            prepareThread={(coordinator) => {
              if (
                !draft.created &&
                (!acpAgent ||
                  draft.acp?.key !== acpKey ||
                  draft.acp.selection.scope !== acp.data?.scope)
              ) {
                draft.acp = undefined;
              }
              return create.mutateAsync(coordinator);
            }}
            onPreparing={setPreparing}
            onSubmitted={openConversation}
            onReviewOutcome={openConversation}
            leadingControls={
              <RunEnvironments
                catalog={selectors.data}
                configuration={preview.data?.configuration}
                value={composerDraft.environment}
                disabled={choicesDisabled}
                onChange={(value) => {
                  composerDraft.environment = value;
                  composerDraft.notify();
                }}
              />
            }
            controls={() => {
              const modelOption = acpModelOption(acp.data?.options);
              return (
                <ThreadRunChoices
                  catalog={selectors.data}
                  agentId={defaults.agent_id ?? ""}
                  defaultAgentId={effectiveAgent?.agent_id}
                  defaultModelId={effective?.default_model_id}
                  modelId={composerDraft.modelId}
                  controls={composerDraft.controls}
                  onControlsChange={runChoices.onControlsChange}
                  disabled={choicesDisabled}
                  onAgentChange={(value) => {
                    change({ agent_id: value || null });
                  }}
                  onExternalOptionsOpen={() => {
                    if (
                      !draft.created &&
                      !acpUpdating &&
                      !acpModelOption(acp.data?.options)
                    )
                      void acp.refetch({ cancelRefetch: false });
                  }}
                  externalLoading={
                    acpEnabled && (acpUpdating || acp.isFetching)
                  }
                  externalModel={acpOptionLabel(
                    modelOption,
                    t("No model selected"),
                  )}
                  externalModelId={
                    modelOption && modelOption.value !== ""
                      ? String(modelOption.value)
                      : undefined
                  }
                  externalThinking={acpOptionLabel(
                    acp.data?.options.find(
                      (option) =>
                        option.category === "thought_level" ||
                        option.id === "thought_level",
                    ),
                  )}
                  externalOptions={
                    acpAgent && (
                      <AcpOptionFields
                        key={acpKey}
                        options={acp.data?.options}
                        preset={effectiveAgent?.preset}
                        loading={acp.isFetching}
                        disabled={choicesDisabled || acpUpdating}
                        error={acpUpdateError ?? acp.error}
                        onChange={(change) =>
                          acp.data &&
                          acpChange.mutate({
                            change,
                            data: acp.data,
                            key: acpKey,
                            queryKey: acpQueryKey,
                            params: acpParams,
                          })
                        }
                      />
                    )
                  }
                  onModelChange={runChoices.onModelChange}
                />
              );
            }}
          />
          {drafts.error && (
            <p role="alert">{composerMessage(drafts.error, t)}</p>
          )}
          {attachmentSelections(composerDraft.doc).some(
            ({ key, id }) =>
              !isReadyAttachment(id) && !composerDraft.uploads.has(key),
          ) && (
            <p role="alert">
              {t(
                "Reattach missing files before sending. Local attachments are lost on reload; uploaded files stay with their original chat.",
              )}
            </p>
          )}
          <ErrorNotice
            error={
              owner.error || preview.error || selectors.error || projects.error
            }
            retry={() => {
              if (ownerId) void owner.refetch();
              void preview.refetch();
              void selectors.refetch();
              void projects.refetch();
            }}
          />
          {(preview.error || setup.data?.needed) && (
            <Link className={styles.setup} to="/setup">
              {t("Continue setup")}
            </Link>
          )}
        </div>
      </section>
    </ConversationOpening>
  );
}

/** Reports true once `active` has held for `delay`, so a state that resolves on
 *  its own never shows anything before it does. */
function useSettled(active: boolean, delay = 300) {
  const [settled, setSettled] = useState(false);
  useEffect(() => {
    if (!active) {
      setSettled(false);
      return;
    }
    const timer = window.setTimeout(() => setSettled(true), delay);
    return () => window.clearTimeout(timer);
  }, [active, delay]);
  return active && settled;
}

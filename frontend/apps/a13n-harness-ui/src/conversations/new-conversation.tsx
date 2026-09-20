import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Link,
  useLocation,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Robot, Folder } from "@phosphor-icons/react";
import { SearchPicker } from "a13n-ui";
import {
  useProjects,
  useSelectors,
  useSetup,
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
import { Composer, ComposerDrafts, useDraft } from "./composer";
import { NewDraftStore, type NewDraft } from "./new-draft";
import { attachmentSelections, isReadyAttachment } from "./inline-attachments";
import { RunEnvironments, ThreadRunChoices } from "./thread-run-choices";
import { refreshThreadLists } from "./queries";
import { ConversationTranscript } from "./transcript";
import { ConversationOpening, useInitialReady } from "./opening";
import styles from "./new-conversation.module.css";

export const NewConversationDrafts = createContext(new NewDraftStore());

export function newConversationPath(projectId: string | null = null) {
  return `/new?project=${encodeURIComponent(projectId ?? "")}`;
}

// Creation and admission remain separate. Resolve a lost creation acknowledgement
// by its retained identity, never by allocating another Thread or replaying Send.
export async function ensureConversation(
  transport: Transport,
  threadId: string,
  draft: NewDraft,
) {
  if (draft.pending) return draft.pending;
  if (draft.created) return;
  const create = async () => {
    if (draft.attempted) {
      try {
        await result(
          transport.client.GET("/api/threads/{thread_id}", {
            params: { path: { thread_id: threadId } },
          }),
        );
        draft.created = true;
        throw new Error(
          "This conversation already exists. Open it to review its settings before sending your retained input.",
        );
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) {
          draft.attempted = false;
          throw new Error(
            "The conversation was not found. Your input is retained; Send again to retry with the same identity.",
          );
        }
        throw error;
      }
    }
    draft.attempted = true;
    draft.save();
    try {
      const created = await result(
        transport.client.POST("/api/threads", {
          body: { thread_id: threadId, defaults: draft.defaults },
        }),
      );
      if (created.thread_id !== threadId)
        throw new Error(
          "The conversation acknowledgement was incomplete. Send again to check its identity.",
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
}) {
  const { draftId } = useParams();
  const drafts = useContext(NewConversationDrafts);
  const composers = useContext(ComposerDrafts);
  const { key } = useLocation();
  // A new navigation can leave a retired composer behind after a successful
  // Send whose saved-page read failed; ordinary rerenders must not replace it.
  const draft = useMemo(
    () => drafts.get(composers, draftId),
    [drafts, composers, draftId, key],
  );
  return <NewConversation key={draft.threadId} draft={draft} {...props} />;
}

function NewConversation({
  draft,
  profile,
  unauthorized,
}: {
  draft: NewDraft;
  profile: Profile;
  unauthorized: () => void;
}) {
  const { threadId } = draft;
  const active = useRef(true);
  useEffect(() => {
    active.current = true;
    return () => {
      active.current = false;
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
    mutationFn: () => ensureConversation(transport, threadId, draft),
    onSettled: (_data, error) => {
      if (draft.created) void refreshThreadLists(queries);
      if (draft.created && error && active.current) openConversation();
    },
  });
  const [search, setSearch] = useSearchParams();
  const requestedProject = search.get("project");
  useEffect(() => {
    // Home resumes the slot; a Project's plus explicitly changes only Project.
    if (requestedProject === null || preparing || draft.attempted) {
      if (requestedProject !== (draft.defaults.project_id ?? ""))
        setSearch(
          (current) => {
            const next = new URLSearchParams(current);
            next.set("project", draft.defaults.project_id ?? "");
            return next;
          },
          { replace: true },
        );
      return;
    }
    if ((draft.defaults.project_id ?? null) === (requestedProject || null))
      return;
    draft.defaults = {
      ...draft.defaults,
      project_id: requestedProject || null,
    };
    setDefaults(draft.defaults);
    draft.save();
  }, [requestedProject, preparing, draft, setSearch]);
  const change = (patch: Schema<"NewThreadDefaults">) => {
    if (preparing || draft.attempted) return;
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
  const effectiveAgent = selectors.data?.agents.find(
    (item) => item.agent_id === effective?.agent_source.id,
  );
  const effectiveEnvironment = selectors.data?.environments.find(
    (item) => item.profile_id === effective?.environment_profile_id,
  );
  const choicesDisabled = preparing || draft.attempted;
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
  const openConversation = () => {
    // Admission and page observation are independent. Open the retained identity
    // immediately so detail, history, focused output and the shared editor can
    // initialize together behind the destination's first-observation gate.
    if (!active.current) return;
    navigate(`/threads/${encodeURIComponent(threadId)}?compose=1`, {
      replace: true,
    });
  };
  return (
    <ConversationOpening
      ready={pageReady}
      label="Preparing your conversation…"
      onContinue={() => setShowAvailable(true)}
    >
      <section className={styles.page} aria-label="New conversation">
        <div className={styles.welcome}>
          <Robot aria-hidden="true" />
          <h1>
            What would you like to build
            {project ? (
              <>
                {" "}
                in <span>{project.name}</span>
              </>
            ) : (
              ""
            )}
            ?
          </h1>
        </div>
        <div className={styles.inputArea}>
          <ConversationTranscript
            entries={[]}
            blocks={[]}
            localInputs={composerDraft.localInputs}
            threadId={threadId}
          />
          <fieldset
            className={styles.context}
            disabled={preparing || draft.attempted}
          >
            <legend className={styles.srOnly}>Conversation settings</legend>
            <div className={styles.location}>
              <Folder aria-hidden="true" />
              <SearchPicker
                label="Project"
                popupClassName={styles.choicePopup}
                placeholder="Without a project"
                emptyMessage="No projects found."
                disabled={preparing || draft.attempted}
                value={defaults.project_id ?? ""}
                onValueChange={(value) => change({ project_id: value || null })}
                groups={[
                  {
                    label: "Projects",
                    options: [
                      { value: "", label: "Without a project" },
                      ...(projects.data ?? []).map((item) => ({
                        value: item.project_id,
                        label: item.name,
                      })),
                      ...(defaults.project_id && !project && !projects.isPending
                        ? [
                            {
                              value: defaults.project_id,
                              label: `${defaults.project_id} (unavailable)`,
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
          <Composer
            autoFocus={pageReady}
            threadId={threadId}
            activity={{ state: "inactive" }}
            canRun={!!preview.data && !preview.error}
            unavailableReason={
              preview.isPending
                ? "Updating conversation settings…"
                : preview.error
                  ? "Review conversation settings before sending."
                  : undefined
            }
            profile={profile}
            unauthorized={unauthorized}
            reconcile={() => {
              void refreshThreadLists(queries);
            }}
            local={!draft.created}
            skillDefaults={defaults}
            prepareThread={() => create.mutateAsync()}
            onPreparing={setPreparing}
            onSubmitted={openConversation}
            onReviewOutcome={openConversation}
            modelId={composerDraft.modelId}
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
            controls={() => (
              <ThreadRunChoices
                catalog={selectors.data}
                agentId={defaults.agent_id ?? ""}
                defaultAgentId={effectiveAgent?.agent_id}
                modelId={composerDraft.modelId}
                thinking={composerDraft.thinking}
                fast={composerDraft.fast}
                onFastChange={(value) => {
                  composerDraft.fast = value;
                  composerDraft.notify();
                }}
                disabled={choicesDisabled}
                onAgentChange={(value) => {
                  change({ agent_id: value || null });
                  composerDraft.thinking = null;
                  composerDraft.fast = null;
                  composerDraft.notify();
                }}
                onModelChange={(value) => {
                  composerDraft.modelId = value;
                  composerDraft.thinking = null;
                  composerDraft.fast = null;
                  composerDraft.notify();
                }}
                onThinkingChange={(value) => {
                  composerDraft.thinking = value;
                  composerDraft.notify();
                }}
              />
            )}
          />
          {drafts.error && <p role="alert">{drafts.error}</p>}
          {attachmentSelections(composerDraft.doc).some(
            ({ key, id }) =>
              !isReadyAttachment(id) && !composerDraft.uploads.has(key),
          ) && (
            <p role="alert">
              Some attachments are unavailable. Local files are not saved across
              reloads, and uploaded files belong to their original conversation.
              Remove unavailable attachments and attach the files again before
              sending.
            </p>
          )}
          <ErrorNotice
            error={preview.error || selectors.error || projects.error}
            retry={() => {
              void preview.refetch();
              void selectors.refetch();
              void projects.refetch();
            }}
          />
          {draft.created && !preparing && (
            <Link to={`/threads/${encodeURIComponent(threadId)}?compose=1`}>
              Open conversation with retained input
            </Link>
          )}
          {(preview.error || setup.data?.needed) && (
            <Link className={styles.setup} to="/setup">
              Continue setup
            </Link>
          )}
        </div>
      </section>
    </ConversationOpening>
  );
}

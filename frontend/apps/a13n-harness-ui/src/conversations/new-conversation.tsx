import { createContext, useContext, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Robot, Folder, Monitor, ShieldWarning } from "@phosphor-icons/react";
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
import { Composer, useDraft } from "./composer";
import { ModelPicker } from "./model-picker";
import { refreshThreadLists } from "./queries";
import { ConversationTranscript } from "./transcript";
import styles from "./new-conversation.module.css";

export type NewDraft = {
  threadId: string;
  defaults: Schema<"NewThreadDefaults">;
  created: boolean;
  attempted: boolean;
  pending?: Promise<void>;
};
export const NewConversationDrafts = createContext(new Map<string, NewDraft>());

export function newConversationPath(projectId: string | null = null) {
  const id = `thread_${crypto.randomUUID().replaceAll("-", "")}`;
  return `/new/${id}${projectId ? `?project=${encodeURIComponent(projectId)}` : ""}`;
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
  }
}

export function NewConversationPage(props: {
  profile: Profile;
  unauthorized: () => void;
}) {
  const { draftId } = useParams();
  const drafts = useContext(NewConversationDrafts);
  const [homeId] = useState(() => {
    let home = drafts.get("@home");
    if (!home) {
      home = {
        threadId: `thread_${crypto.randomUUID().replaceAll("-", "")}`,
        defaults: { project_id: null },
        created: false,
        attempted: false,
      };
      drafts.set("@home", home);
      drafts.set(home.threadId, home);
    }
    return home.threadId;
  });
  const [search] = useSearchParams();
  return (
    <NewConversation
      key={draftId ?? homeId}
      threadId={draftId ?? homeId}
      projectId={search.get("project")}
      {...props}
    />
  );
}

function NewConversation({
  threadId,
  projectId,
  profile,
  unauthorized,
}: {
  threadId: string;
  projectId: string | null;
  profile: Profile;
  unauthorized: () => void;
}) {
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
  const [draft] = useState(() => {
    let retained = drafts.get(threadId);
    if (!retained) {
      retained = {
        threadId,
        defaults: { project_id: projectId },
        created: false,
        attempted: false,
      };
      drafts.set(threadId, retained);
    }
    return retained;
  });
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
      if (draft.created && error && active.current)
        void openConversation().catch(() => {});
    },
  });
  const [search, setSearch] = useSearchParams();
  useEffect(() => {
    if (search.get("project") === (defaults.project_id ?? null)) return;
    setSearch(
      (current) => {
        if (defaults.project_id) current.set("project", defaults.project_id);
        else current.delete("project");
        return current;
      },
      { replace: true },
    );
  }, [defaults.project_id, search, setSearch]);
  const change = (patch: Schema<"NewThreadDefaults">) => {
    if (preparing || draft.attempted) return;
    draft.defaults = { ...defaults, ...patch };
    setDefaults(draft.defaults);
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
  const isolation =
    effectiveEnvironment?.mode === "full-control"
      ? "Full Control"
      : effectiveEnvironment?.mode === "sandbox"
        ? "Sandbox"
        : "Custom environment";
  const choicesDisabled = preparing || draft.attempted;
  const openConversation = async () => {
    // Keep the current composer visible until the saved route has its first frame.
    // Never retry admission here: this is only an exact-identity observation.
    const detail = await queries.fetchQuery({
      queryKey: ["thread", threadId, "detail"],
      queryFn: () =>
        result(
          transport.client.GET("/api/threads/{thread_id}", {
            params: { path: { thread_id: threadId } },
          }),
        ),
      staleTime: 0,
    });
    await queries.prefetchInfiniteQuery({
      queryKey: ["thread", threadId, "history", detail.continuation_id],
      initialPageParam: undefined as string | undefined,
      queryFn: () =>
        result(
          transport.client.GET("/api/threads/{thread_id}/transcript", {
            params: {
              path: { thread_id: threadId },
              query: {
                expected_continuation_id: detail.continuation_id ?? undefined,
                limit: 30,
              },
            },
          }),
        ),
    });
    if (!active.current) return;
    if (drafts.get("@home") === draft) drafts.delete("@home");
    navigate(`/threads/${encodeURIComponent(threadId)}?compose=1`, {
      replace: true,
    });
  };
  return (
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
          <div className={styles.location}>
            <Monitor aria-hidden="true" />
            <SearchPicker
              label="Environment"
              popupClassName={styles.choicePopup}
              placeholder={effectiveEnvironment?.name ?? "Default environment"}
              emptyMessage="No environments found."
              disabled={preparing || draft.attempted}
              value={defaults.environment_profile_id ?? ""}
              onValueChange={(value) =>
                change({ environment_profile_id: value || null })
              }
              groups={[
                {
                  label: "Environments",
                  options: [
                    {
                      value: "",
                      label:
                        !defaults.environment_profile_id && effectiveEnvironment
                          ? `Default · ${effectiveEnvironment.name}`
                          : "Default environment",
                      description: "Follow the project or app default.",
                    },
                    ...(selectors.data?.environments ?? []).map((item) => ({
                      value: item.profile_id,
                      label: item.name,
                      description: item.description,
                    })),
                  ],
                },
              ]}
            />
          </div>
        </fieldset>
        <Composer
          autoFocus
          threadId={threadId}
          activity={{ state: "inactive" }}
          canRun={!!preview.data && !preview.isFetching && !preview.error}
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
          modelId={composerDraft.modelId}
          leadingControls={
            effectiveEnvironment && (
              <span
                className={styles.mode}
                data-full-control={effectiveEnvironment.mode === "full-control"}
                title={
                  effectiveEnvironment.mode === "full-control"
                    ? "Runs on the host with your account permissions."
                    : effectiveEnvironment.description
                }
              >
                <ShieldWarning aria-hidden="true" />
                {isolation}
              </span>
            )
          }
          controls={
            <div className={styles.runChoices}>
              <div className={styles.runChoice}>
                <span>Agent</span>
                <SearchPicker
                  label="Agent"
                  popupClassName={styles.choicePopup}
                  placeholder={
                    effectiveAgent
                      ? `Default · ${effectiveAgent.name}`
                      : "Default agent"
                  }
                  emptyMessage="No agents found."
                  disabled={choicesDisabled}
                  value={defaults.agent_id ?? ""}
                  onValueChange={(value) => change({ agent_id: value || null })}
                  groups={[
                    {
                      label: "Agents",
                      options: [
                        {
                          value: "",
                          label:
                            !defaults.agent_id && effectiveAgent
                              ? `Default · ${effectiveAgent.name}`
                              : "Default agent",
                          description: "Follow the project or app default.",
                        },
                        ...(selectors.data?.agents ?? []).map((item) => ({
                          value: item.agent_id,
                          label: item.name,
                          description: item.agent_id,
                        })),
                      ],
                    },
                  ]}
                />
              </div>
              <ModelPicker
                models={selectors.data?.models ?? []}
                defaultModelId={effectiveAgent?.model_id ?? undefined}
                value={composerDraft.modelId}
                disabled={choicesDisabled}
                onChange={(value) => {
                  composerDraft.modelId = value;
                  composerDraft.notify();
                }}
              />
            </div>
          }
        />
        <ErrorNotice
          error={preview.error || selectors.error || projects.error}
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
  );
}

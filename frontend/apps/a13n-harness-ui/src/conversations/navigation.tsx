import { useEffect, useState } from "react";
import { Link, NavLink, useMatch, useNavigate } from "react-router";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Button,
  Checkbox,
  Collapsible,
  CollapsibleTrigger,
  CollapsiblePanel,
  FormField,
  Input,
  SearchPicker,
  Skeleton,
  ModalFrame,
  Menu,
  MenuTrigger,
  MenuPopup,
  MenuItem,
} from "a13n-ui";
import {
  Plus,
  CaretRight,
  FolderSimple,
  WarningCircle,
  CircleNotch,
  Question,
  Archive,
  ChatCircle,
  Gear,
  DotsThree,
  PencilSimple,
  ShareNetwork,
  SlidersHorizontal,
} from "@phosphor-icons/react";
import { useProjects, useSelectors, useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice, TextField } from "../shell/ui";
import { useThreads } from "./queries";
import { ResourceChoice } from "../configuration/resource-choice";
import {
  ParticipantAvatars,
  threadParticipants,
} from "../shell/participant-avatars";
import styles from "./conversation.module.css";

export function ConversationNavigation({
  presence = null,
}: {
  presence?: Schema<"PresenceFrame"> | null;
}) {
  const projects = useProjects();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [archived, setArchived] = useState(false);
  const [scope, setScope] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string | null>>(new Set());
  const match = useMatch("/threads/:threadId");
  const [creating, setCreating] = useState<string | null | undefined>(
    undefined,
  );
  const list = useThreads(query, scope || undefined, archived);
  const rows = list.data?.pages.flatMap((page) => page.rows) ?? [];
  const groups = new Map<string | null, { name: string; rows: typeof rows }>();
  for (const project of projects.data ?? [])
    if (!scope || project.project_id === scope)
      groups.set(project.project_id, { name: project.name, rows: [] });
  if (!scope) groups.set(null, { name: "Without a project", rows: [] });
  for (const row of rows) {
    const id = row.thread.configuration.project_id ?? null;
    if (!groups.has(id))
      groups.set(id, {
        name: row.project_name || "Unavailable project",
        rows: [],
      });
    groups.get(id)!.rows.push(row);
  }
  const activeThread = rows.find(
    (row) => row.thread.thread_id === match?.params.threadId,
  );
  const activeProject = activeThread?.thread.configuration.project_id ?? null;
  useEffect(() => {
    if (match)
      setCollapsed((current) => {
        const next = new Set(current);
        next.delete(activeProject);
        return next;
      });
  }, [match?.params.threadId, activeProject]);
  return (
    <div className={styles.navigation}>
      <Button variant="outline" onClick={() => setCreating(null)}>
        <Plus />
        New conversation
      </Button>
      <div className={styles.navigationFilters}>
        <FormField label="Find conversations" hideLabel>
          <Input
            type="search"
            placeholder="Find conversations…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </FormField>
        <SearchPicker
          label="Project scope"
          placeholder="All Projects"
          emptyMessage="No matching projects."
          value={scope}
          onValueChange={setScope}
          groups={[
            {
              label: "Projects",
              options: [
                { value: "", label: "All Projects", icon: <FolderSimple /> },
                ...(projects.data ?? []).map((project) => ({
                  value: project.project_id,
                  label: project.name,
                  keywords: [project.project_id],
                  icon: <FolderSimple />,
                })),
              ],
            },
          ]}
        />
      </div>
      <div className={`${styles.threadGroups} a13n-scrollbar`}>
        {Array.from(groups)
          .filter(([, group]) => !query || group.rows.length)
          .map(([id, group]) => (
            <Collapsible
              key={id ?? "projectless"}
              open={!!query || !collapsed.has(id)}
              onOpenChange={(open) =>
                setCollapsed((current) => {
                  const next = new Set(current);
                  if (open) next.delete(id);
                  else next.add(id);
                  return next;
                })
              }
            >
              <div className={styles.groupHeading}>
                <CollapsibleTrigger
                  className={styles.groupToggle}
                  title={group.name}
                  aria-label={`Conversations in ${group.name}`}
                  disabled={!!query}
                >
                  <CaretRight />
                  <span>{group.name}</span>
                </CollapsibleTrigger>
                <div className={styles.groupActions}>
                  {id && (
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label={`Settings for ${group.name}`}
                      render={
                        <Link to={`/projects/${encodeURIComponent(id)}`} />
                      }
                    >
                      <Gear />
                    </Button>
                  )}
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`New conversation in ${group.name}`}
                    onClick={() => setCreating(id)}
                  >
                    <Plus />
                  </Button>
                </div>
              </div>
              <CollapsiblePanel>
                {group.rows.map((row) => (
                  <div key={row.thread.thread_id} className={styles.threadRow}>
                    <NavLink
                      to={`/threads/${encodeURIComponent(row.thread.thread_id)}`}
                      className={({ isActive }) =>
                        `${styles.threadLink} ${isActive ? styles.selected : ""}`
                      }
                    >
                      <ThreadStateIcon row={row} />
                      <span>
                        <strong
                          title={
                            row.thread.title ||
                            row.thread.excerpt?.first_input ||
                            "Untitled conversation"
                          }
                        >
                          {row.thread.title ||
                            row.thread.excerpt?.first_input ||
                            "Untitled conversation"}
                        </strong>
                        {threadState(row) && <small>{threadState(row)}</small>}
                      </span>
                    </NavLink>
                    <ParticipantAvatars
                      participants={threadParticipants(
                        presence,
                        row.thread.thread_id,
                      )}
                      ownId={presence?.participant_id}
                      threadTitle={
                        row.thread.title ||
                        row.thread.excerpt?.first_input ||
                        "Untitled conversation"
                      }
                    />
                    <Menu>
                      <MenuTrigger
                        render={<Button variant="ghost" size="icon-sm" />}
                        className={styles.threadActions}
                        aria-label={`Actions for ${row.thread.title || "Untitled conversation"}`}
                      >
                        <DotsThree />
                      </MenuTrigger>
                      <MenuPopup align="start" side="right">
                        {(
                          [
                            ["rename", "Rename conversation", PencilSimple],
                            ["share", "Share conversation", ShareNetwork],
                            ["comments", "Comments", ChatCircle],
                            [
                              "details",
                              "Conversation details",
                              SlidersHorizontal,
                            ],
                          ] as const
                        ).map(([action, label, Icon]) => (
                          <MenuItem
                            key={action}
                            onClick={() =>
                              navigate(
                                `/threads/${encodeURIComponent(row.thread.thread_id)}?dialog=${action}`,
                              )
                            }
                          >
                            <Icon />
                            {label}
                          </MenuItem>
                        ))}
                      </MenuPopup>
                    </Menu>
                  </div>
                ))}
                {!group.rows.length && !query && list.isSuccess && (
                  <small className={styles.emptyGroup}>
                    No conversations yet
                  </small>
                )}
              </CollapsiblePanel>
            </Collapsible>
          ))}
        {list.hasNextPage && (
          <Button
            variant="ghost"
            loading={list.isFetchingNextPage}
            onClick={() => void list.fetchNextPage()}
          >
            Load more conversations
          </Button>
        )}
        {list.isPending && (
          <div
            role="status"
            aria-label="Loading conversations"
            aria-busy="true"
            className={styles.threadSkeletons}
          >
            {[0, 1, 2, 3].map((item) => (
              <Skeleton key={item} className="h-8 w-full" />
            ))}
          </div>
        )}
        {list.isSuccess && !rows.length && query && (
          <p>No matching conversations.</p>
        )}
        <ErrorNotice error={list.error} retry={() => void list.refetch()} />
      </div>
      <label className={styles.archiveFilter}>
        <Checkbox checked={archived} onCheckedChange={setArchived} />
        Include archived
      </label>
      {creating !== undefined && (
        <NewConversation
          projectId={creating}
          close={() => setCreating(undefined)}
        />
      )}
    </div>
  );
}

function threadState(row: Schema<"ThreadActivityView">) {
  if (row.pending_decision) return "Needs your answer";
  if (row.thread.root_activity.state === "preparing") return "Preparing";
  if (row.thread.root_activity.state === "running") return "Running";
  if (row.latest_operation?.status === "failed") return "Failed";
  if (row.thread.archived) return "Archived";
  return "";
}

function ThreadStateIcon({ row }: { row: Schema<"ThreadActivityView"> }) {
  if (row.pending_decision)
    return <Question className={styles.threadWaiting} aria-hidden="true" />;
  if (row.thread.root_activity.state !== "inactive")
    return <CircleNotch className={styles.threadRunning} aria-hidden="true" />;
  if (row.latest_operation?.status === "failed")
    return <WarningCircle className={styles.threadFailed} aria-hidden="true" />;
  return row.thread.archived ? (
    <Archive aria-hidden="true" />
  ) : (
    <ChatCircle aria-hidden="true" />
  );
}

export function NewConversation({
  projectId,
  close,
}: {
  projectId: string | null;
  close: () => void;
}) {
  const transport = useTransport();
  const selectors = useSelectors();
  const projects = useProjects();
  const navigate = useNavigate();
  const [project, setProject] = useState(projectId ?? "");
  const [title, setTitle] = useState("");
  const [agent, setAgent] = useState("");
  const [environment, setEnvironment] = useState("");
  const defaults: Schema<"NewThreadDefaults"> = {
    project_id: project || null,
    ...(agent ? { agent_id: agent } : {}),
    ...(environment ? { environment_profile_id: environment } : {}),
  };
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
    mutationFn: () =>
      result(
        transport.client.POST("/api/threads", {
          body: { title: title || null, defaults },
        }),
      ),
    onSuccess: (thread) => {
      close();
      navigate(`/threads/${encodeURIComponent(thread.thread_id)}`);
    },
  });
  return (
    <ModalFrame
      open
      onOpenChange={(open) => {
        if (!open && !create.isPending) close();
      }}
      title="New conversation"
      description="Choose where to work. These selections are captured for the new conversation; creating it does not start a model."
      closeLabel="Close"
    >
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          if (
            preview.data &&
            !preview.isFetching &&
            !preview.error &&
            !create.isPending
          )
            create.mutate();
        }}
      >
        <TextField
          label="Title (optional)"
          value={title}
          onChange={(value) => setTitle(value.slice(0, 200))}
        />
        <ResourceChoice
          label="Project"
          value={project}
          onValueChange={setProject}
          options={[
            { value: "", label: "Without a project" },
            ...(projects.data ?? []).map((item) => ({
              value: item.project_id,
              label: item.name,
            })),
          ]}
        />
        <ResourceChoice
          label="Agent"
          value={agent}
          onValueChange={setAgent}
          options={[
            { value: "", label: "Use creation default" },
            ...(selectors.data?.agents ?? []).map((item) => ({
              value: item.agent_id,
              label: item.name,
            })),
          ]}
        />
        <ResourceChoice
          label="Environment"
          value={environment}
          onValueChange={setEnvironment}
          options={[
            { value: "", label: "Use creation default" },
            ...(selectors.data?.environments ?? []).map((item) => ({
              value: item.profile_id,
              label: item.name,
            })),
          ]}
        />
        {preview.data && (
          <div className={styles.summary}>
            <p>
              Agent:{" "}
              {selectors.data?.agents.find(
                (item) =>
                  item.agent_id === preview.data.configuration.agent_source.id,
              )?.name || preview.data.configuration.agent_source.id}{" "}
              · {preview.data.provenance.agent_source}
            </p>
            <p>
              Environment: {preview.data.configuration.environment_profile_id} ·{" "}
              {preview.data.provenance.environment_profile_id}
            </p>
          </div>
        )}
        <ErrorNotice error={preview.error || create.error || selectors.error} />
        {preview.error && (
          <Link to="/setup" onClick={close}>
            Open setup and readiness
          </Link>
        )}
        <Button
          type="submit"
          loading={create.isPending}
          disabled={!preview.data || preview.isFetching || !!preview.error}
        >
          Create conversation
        </Button>
      </form>
    </ModalFrame>
  );
}

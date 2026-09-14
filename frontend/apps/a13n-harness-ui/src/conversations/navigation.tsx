import { useState } from "react";
import { Link, NavLink, useNavigate } from "react-router";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  Button,
  ChoiceField,
  ModalFrame,
  Menu,
  MenuTrigger,
  MenuPopup,
  MenuItem,
} from "a13n-ui";
import {
  Plus,
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
import styles from "./conversation.module.css";

export function ConversationNavigation() {
  const projects = useProjects();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [archived, setArchived] = useState(false);
  const [scope, setScope] = useState("");
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
  return (
    <div className={styles.navigation}>
      <Button variant="outline" onClick={() => setCreating(null)}>
        <Plus />
        New conversation
      </Button>
      <TextField
        type="search"
        label="Find conversations"
        value={query}
        onChange={setQuery}
      />
      <ChoiceField
        label="Project scope"
        value={scope}
        onValueChange={setScope}
        options={[
          { value: "", label: "All Projects" },
          ...(projects.data ?? []).map((project) => ({
            value: project.project_id,
            label: project.name,
          })),
        ]}
      />
      <div className={`${styles.threadGroups} a13n-scrollbar`}>
        {Array.from(groups, ([id, group]) => (
          <section key={id ?? "projectless"}>
            <div className={styles.groupHeading}>
              <span>{group.name}</span>
              <div>
                {id && (
                  <Button
                    variant="ghost"
                    size="icon"
                    aria-label={`Settings for ${group.name}`}
                    render={<Link to={`/projects/${encodeURIComponent(id)}`} />}
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
            {group.rows.map((row) => (
              <div key={row.thread.thread_id} className={styles.threadRow}>
                <NavLink
                  to={`/threads/${encodeURIComponent(row.thread.thread_id)}`}
                  className={({ isActive }) =>
                    `${styles.threadLink} ${isActive ? styles.selected : ""}`
                  }
                >
                  <ChatCircle />
                  <span>
                    <strong>
                      {row.thread.title ||
                        row.thread.excerpt?.first_input ||
                        "Untitled conversation"}
                    </strong>
                    <small>
                      {row.pending_decision
                        ? "Needs your answer"
                        : row.thread.root_activity.state !== "inactive"
                          ? row.thread.root_activity.state
                          : row.latest_operation?.status === "failed"
                            ? "Failed"
                            : row.thread.archived
                              ? "Archived"
                              : ""}
                    </small>
                  </span>
                </NavLink>
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
                        ["details", "Conversation details", SlidersHorizontal],
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
            {!group.rows.length && !query && !list.isPending && (
              <small className={styles.emptyGroup}>No conversations yet</small>
            )}
          </section>
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
        {list.isPending && <p role="status">Loading conversations…</p>}
        {!list.isPending && !rows.length && query && (
          <p>No matching conversations.</p>
        )}
        <ErrorNotice error={list.error} retry={() => void list.refetch()} />
      </div>
      <label className={styles.archiveFilter}>
        <input
          type="checkbox"
          checked={archived}
          onChange={(event) => setArchived(event.target.checked)}
        />
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
        <ChoiceField
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
        <ChoiceField
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
        <ChoiceField
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

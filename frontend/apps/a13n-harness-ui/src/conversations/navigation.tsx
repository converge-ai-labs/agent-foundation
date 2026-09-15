import { useEffect, useState } from "react";
import { useMatch, useNavigate } from "react-router";
import {
  Button,
  Checkbox,
  FormField,
  Input,
  SearchPicker,
  Menu,
  MenuTrigger,
  MenuPopup,
  MenuItem,
} from "a13n-ui";
import {
  Plus,
  Folder,
  CaretRight,
  DotsThree,
  DotsSixVertical,
  Gear,
  ArrowUp,
  ArrowDown,
  PencilSimpleIcon,
} from "@phosphor-icons/react";
import { useProjects } from "../transport/context";
import type { Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { useThread, useThreads } from "./queries";
import { useProjectExpansion, useProjectOrder } from "./project-order";
import { NewProject } from "./new-project";
import { RenameProject } from "../configuration/rename-project";
import { newConversationPath } from "./new-conversation";
import { ThreadRow } from "./thread-row";
import styles from "./conversation.module.css";

type Presence = Schema<"PresenceFrame"> | null;
type Group = {
  id: string;
  name: string;
  projectId?: string;
  scope?: "projectless" | "unavailable";
};

export function ConversationNavigation({
  presence = null,
}: {
  presence?: Presence;
}) {
  const projects = useProjects();
  const navigate = useNavigate();
  const match = useMatch("/threads/:threadId");
  const selectedId = match?.params.threadId ?? "";
  const selected = useThread(selectedId).data?.thread;
  const [query, setQuery] = useState("");
  const [archived, setArchived] = useState(false);
  const [scope, setScope] = useState("");
  const scopedProject = projects.data?.find(
    (project) => project.project_id === scope,
  );
  const [adding, setAdding] = useState(false);
  const [renaming, setRenaming] = useState<Group | null>(null);
  const [expanded, setExpanded] = useProjectExpansion();
  const order = useProjectOrder(
    (projects.data ?? []).map((project) => project.project_id),
  );
  const groups: Group[] = order.ids.map((id) => {
    const project = projects.data!.find((item) => item.project_id === id)!;
    return { id, projectId: id, name: project.name };
  });
  groups.push({
    id: "@projectless",
    scope: "projectless",
    name: "Without a project",
  });
  groups.push({
    id: "@unavailable",
    scope: "unavailable",
    name: "Unavailable projects",
  });
  const activeGroup =
    selected && projects.data
      ? selected.configuration.project_id == null
        ? "@projectless"
        : projects.data?.some(
              (project) =>
                project.project_id === selected.configuration.project_id,
            )
          ? selected.configuration.project_id
          : "@unavailable"
      : undefined;
  useEffect(() => {
    if (activeGroup) setExpanded(activeGroup, true);
    // Opening a different Thread reveals its group, but refreshes do not undo a manual collapse.
  }, [selectedId, activeGroup, setExpanded]);
  const searching = !!query.trim();
  useEffect(() => {
    if (searching) order.cancel();
  }, [searching, order.cancel]);
  return (
    <div className={styles.navigation}>
      <Button
        variant="ghost"
        onClick={() =>
          navigate(
            newConversationPath(
              scopedProject?.project_id ??
                selected?.configuration.project_id ??
                null,
            ),
          )
        }
      >
        <Plus />
        New conversation
      </Button>
      <Button variant="outline" onClick={() => setAdding(true)}>
        <Plus />
        Add project
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
          value={scopedProject?.project_id ?? ""}
          onValueChange={setScope}
          groups={[
            {
              label: "Projects",
              options: [
                { value: "", label: "All Projects", icon: <Folder /> },
                ...(projects.data ?? []).map((project) => ({
                  value: project.project_id,
                  label: project.name,
                  keywords: [project.project_id],
                  icon: <Folder />,
                })),
              ],
            },
          ]}
        />
      </div>
      <ErrorNotice
        error={projects.error}
        retry={() => void projects.refetch()}
      />
      <div
        className={`${styles.threadGroups} a13n-scrollbar`}
        data-project-scroll
        {...order.pointerEvents}
      >
        {projects.isPending && <p role="status">Loading projects…</p>}
        <div hidden={searching}>
          {groups.map((group) => (
            <ProjectGroup
              key={group.id}
              group={group}
              expanded={
                expanded[group.id] ??
                (activeGroup === group.id ||
                  (projects.isSuccess &&
                    !projects.data.length &&
                    group.id === "@projectless"))
              }
              toggle={(open) => setExpanded(group.id, open)}
              hidden={
                !!scopedProject && scopedProject.project_id !== group.projectId
              }
              enabled={
                !searching &&
                (!scopedProject || scopedProject.project_id === group.projectId)
              }
              archived={archived}
              presence={presence}
              selected={activeGroup === group.id ? selected : undefined}
              create={() =>
                navigate(newConversationPath(group.projectId ?? null))
              }
              order={order}
              rename={() => setRenaming(group)}
            />
          ))}
        </div>
        {searching && (
          <SearchResults
            query={query.trim()}
            project={scopedProject}
            archived={archived}
            presence={presence}
          />
        )}
      </div>
      <span className={styles.srOnly} role="status">
        {order.announcement}
      </span>
      <label className={styles.archiveFilter}>
        <Checkbox checked={archived} onCheckedChange={setArchived} />
        Include archived
      </label>
      {adding && (
        <NewProject
          close={() => setAdding(false)}
          created={(id) => setExpanded(id, true)}
        />
      )}
      {renaming?.projectId && (
        <RenameProject
          projectId={renaming.projectId}
          name={renaming.name}
          close={() => setRenaming(null)}
        />
      )}
    </div>
  );
}

function ProjectGroup({
  group,
  expanded,
  toggle,
  enabled,
  hidden,
  archived,
  presence,
  selected,
  create,
  rename,
  order,
}: {
  group: Group;
  expanded: boolean;
  toggle: (open: boolean) => void;
  enabled: boolean;
  hidden: boolean;
  archived: boolean;
  presence: Presence;
  selected?: Schema<"ThreadSummary">;
  create: () => void;
  rename: () => void;
  order: ReturnType<typeof useProjectOrder>;
}) {
  const navigate = useNavigate();
  const list = useThreads("", group.projectId, archived, {
    scope: group.scope,
    enabled: enabled && expanded,
    limit: 5,
  });
  const rows = [
    ...new Map(
      (list.data?.pages.flatMap((page) => page.rows) ?? []).map((row) => [
        row.thread.thread_id,
        row,
      ]),
    ).values(),
  ];
  const pinned =
    selected && !rows.some((row) => row.thread.thread_id === selected.thread_id)
      ? selected
      : undefined;
  return (
    <section
      hidden={hidden}
      data-project-key={group.projectId}
      aria-label={group.name}
      className={order.moving === group.id ? styles.movingProject : undefined}
    >
      <div className={styles.groupHeading}>
        {group.projectId && (
          <Button
            variant="ghost"
            size="icon-sm"
            className={styles.projectDrag}
            aria-label={`Reorder ${group.name}`}
            aria-pressed={order.moving === group.id}
            title="Drag to reorder, or press Space and use arrow keys"
            {...order.handle(group.id)}
          >
            <DotsSixVertical />
          </Button>
        )}
        <button
          className={styles.groupToggle}
          aria-expanded={expanded}
          onClick={() => toggle(!expanded)}
        >
          <CaretRight
            className={expanded ? styles.expandedChevron : undefined}
          />
          <Folder />
          <span>{group.name}</span>
        </button>
        <div className={styles.groupActions}>
          {expanded && list.isFetching && !!list.data && (
            <span
              role="status"
              className={styles.refreshingGroup}
              aria-label={`Updating conversations in ${group.name}`}
            >
              Updating…
            </span>
          )}
          {group.scope !== "unavailable" && (
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={`New conversation in ${group.name}`}
              onClick={create}
            >
              <Plus />
            </Button>
          )}
          {group.projectId && (
            <Menu>
              <MenuTrigger
                render={<Button variant="ghost" size="icon-sm" />}
                aria-label={`Actions for ${group.name}`}
              >
                <DotsThree />
              </MenuTrigger>
              <MenuPopup align="start" side="right">
                <MenuItem onClick={rename}>
                  <PencilSimpleIcon />
                  Rename project
                </MenuItem>
                <MenuItem
                  onClick={() =>
                    navigate(
                      `/projects/${encodeURIComponent(group.projectId!)}`,
                    )
                  }
                >
                  <Gear />
                  Project settings
                </MenuItem>
                <MenuItem
                  disabled={order.ids.indexOf(group.id) === 0}
                  onClick={() => order.moveBy(group.id, -1)}
                >
                  <ArrowUp />
                  Move project up
                </MenuItem>
                <MenuItem
                  disabled={
                    order.ids.indexOf(group.id) === order.ids.length - 1
                  }
                  onClick={() => order.moveBy(group.id, 1)}
                >
                  <ArrowDown />
                  Move project down
                </MenuItem>
                <MenuItem onClick={order.reset}>
                  Reset project order in this browser
                </MenuItem>
              </MenuPopup>
            </Menu>
          )}
        </div>
      </div>
      <div hidden={!expanded} className={styles.groupThreads}>
        {pinned && (
          <div className={styles.pinnedThread}>
            <small className={styles.emptyGroup}>
              Selected conversation · outside this page
            </small>
            <ThreadRow row={{ thread: pinned }} presence={presence} />
          </div>
        )}
        {rows.map((row) => (
          <ThreadRow key={row.thread.thread_id} row={row} presence={presence} />
        ))}
        {expanded && !list.data && list.isPending && (
          <div
            role="status"
            aria-label="Loading conversations"
            aria-busy="true"
            className={styles.initialLoading}
          >
            <span>Loading conversations…</span>
          </div>
        )}
        {list.isSuccess && !list.isPreviousData && !rows.length && (
          <small className={styles.emptyGroup}>No conversations yet</small>
        )}
        <ErrorNotice error={list.error} retry={() => void list.refetch()} />
        {list.hasNextPage && (
          <Button
            variant="ghost"
            size="sm"
            loading={list.isFetchingNextPage}
            onClick={() => void list.fetchNextPage()}
            aria-label={`Show more conversations in ${group.name}`}
          >
            Show more
          </Button>
        )}
      </div>
    </section>
  );
}

function SearchResults({
  query,
  project,
  archived,
  presence,
}: {
  query: string;
  project?: { project_id: string; name: string };
  archived: boolean;
  presence: Presence;
}) {
  const list = useThreads(query, project?.project_id, archived);
  const rows = [
    ...new Map(
      (list.data?.pages.flatMap((page) => page.rows) ?? []).map((row) => [
        row.thread.thread_id,
        row,
      ]),
    ).values(),
  ];
  return (
    <section aria-label="Conversation search results">
      <small className={styles.emptyGroup}>
        {project ? `Results from ${project.name}` : "Results from all projects"}
      </small>
      {rows.map((row) => (
        <div key={row.thread.thread_id}>
          <small className={styles.emptyGroup}>{row.project_name}</small>
          <ThreadRow row={row} presence={presence} />
        </div>
      ))}
      {list.isFetching && !list.isFetchingNextPage && (
        <p role="status">
          {list.data ? "Updating results…" : "Searching conversations…"}
        </p>
      )}
      {list.isSuccess && !rows.length && <p>No matching conversations.</p>}
      <ErrorNotice error={list.error} retry={() => void list.refetch()} />
      {list.hasNextPage && (
        <Button
          variant="ghost"
          loading={list.isFetchingNextPage}
          onClick={() => void list.fetchNextPage()}
        >
          Show more results
        </Button>
      )}
    </section>
  );
}

import { Fragment, useEffect, useState } from "react";
import { NavLink, useMatch, useNavigate } from "react-router";
import {
  Button,
  FormField,
  Input,
  SearchPicker,
  Popover,
  PopoverTrigger,
  PopoverPopup,
  Menu,
  MenuTrigger,
  MenuPopup,
  MenuItem,
} from "a13n-ui";
import {
  Plus,
  Funnel,
  Folder,
  CaretRight,
  House,
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
import { useThread, useThreads, useThreadOwners } from "./queries";
import { useProjectExpansion, useProjectOrder } from "./project-order";
import { CoordinatorEntry } from "./coordinator-entry";
import { NewProject } from "./new-project";
import { RenameProject } from "../configuration/rename-project";
import { newConversationPath } from "./new-conversation";
import { ThreadRow } from "./thread-row";
import styles from "./conversation.module.css";
import { useResults } from "./results";
import { DraftNavigation } from "./draft-navigation";

type Group = {
  id: string;
  name: string;
  projectId?: string;
  scope?: "projectless" | "unavailable";
};

export function ConversationNavigation() {
  const projects = useProjects();
  const navigate = useNavigate();
  const match = useMatch("/threads/:threadId");
  const selectedId = match?.params.threadId ?? "";
  const selectedDetail = useThread(selectedId);
  const selected = selectedDetail.data?.thread;
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState("");
  const [filterOpen, setFilterOpen] = useState(false);
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
      <div className={styles.navigationFilters}>
        <FormField label="Find conversations" hideLabel>
          <Input
            type="search"
            placeholder="Find conversations…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </FormField>
        <Popover open={filterOpen} onOpenChange={setFilterOpen}>
          <PopoverTrigger
            render={<Button variant="ghost" size="icon-sm" />}
            aria-label="Filter by project"
            title={
              scopedProject
                ? `Project: ${scopedProject.name}`
                : "Filter by project"
            }
            className={scopedProject ? styles.activeFilter : undefined}
          >
            <Funnel weight={scopedProject ? "fill" : "regular"} />
          </PopoverTrigger>
          <PopoverPopup align="end" className={styles.scopePopup}>
            <SearchPicker
              label="Project scope"
              placeholder="All Projects"
              emptyMessage="No matching projects."
              value={scopedProject?.project_id ?? ""}
              onValueChange={(value) => {
                setScope(value);
                setFilterOpen(false);
              }}
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
          </PopoverPopup>
        </Popover>
      </div>
      <nav aria-label="Conversation shortcuts" className={styles.shortcuts}>
        <NavLink
          to="/"
          end
          className={({ isActive }) =>
            `${styles.homeLink} ${isActive ? styles.selected : ""}`
          }
        >
          <House size={18} />
          <span>Home</span>
        </NavLink>
        <DraftNavigation />
      </nav>
      <div className={styles.navigationHeading}>
        <span title={scopedProject?.name}>
          {scopedProject?.name ?? "Projects"}
        </span>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Add project"
          title="Add project"
          onClick={() => setAdding(true)}
        >
          <Plus />
        </Button>
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
              selected={activeGroup === group.id ? selected : undefined}
              selectedUpdatedAt={selectedDetail.dataUpdatedAt}
              create={() =>
                navigate(newConversationPath(group.projectId ?? null))
              }
              order={order}
              rename={() => setRenaming(group)}
            />
          ))}
        </div>
        {searching && (
          <SearchResults query={query.trim()} project={scopedProject} />
        )}
      </div>
      <span className={styles.srOnly} role="status">
        {order.announcement}
      </span>
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
  selected,
  selectedUpdatedAt,
  create,
  rename,
  order,
}: {
  group: Group;
  expanded: boolean;
  toggle: (open: boolean) => void;
  enabled: boolean;
  hidden: boolean;
  selected?: Schema<"ThreadSummary">;
  selectedUpdatedAt: number;
  create: () => void;
  rename: () => void;
  order: ReturnType<typeof useProjectOrder>;
}) {
  const navigate = useNavigate();
  const results = useResults();
  const [markingRead, setMarkingRead] = useState(false);
  const projects = useProjects();
  const belongs = (thread: Schema<"ThreadSummary">) => {
    const project = thread.configuration.project_id;
    return group.scope === "projectless"
      ? project == null
      : group.scope === "unavailable"
        ? project != null &&
          !!projects.data &&
          !projects.data.some((item) => item.project_id === project)
        : project === group.projectId;
  };
  const list = useThreads("", group.projectId, false, {
    scope: group.scope,
    enabled: enabled && expanded,
    limit: 5,
    includeActive: true,
    includeStarred: true,
  });
  // The selected detail can arrive before a slower sidebar refresh. Use that
  // observation for this row, without replacing other Projects or page cursors.
  type Row = Pick<Schema<"ThreadActivityView">, "thread"> &
    Partial<Schema<"ThreadActivityView">>;
  const observed = new Map<string, Row>(
    [
      ...(list.data?.pages.flatMap((page) => page.rows) ?? []),
      ...(list.data?.pages[0]?.active_rows ?? []),
      ...(list.data?.pages[0]?.starred_rows ?? []),
    ].map((row) => [row.thread.thread_id, row]),
  );
  for (const { thread, observedAt } of results.threads.values()) {
    const page = list.data?.pages.find((page, index) =>
      [
        ...page.rows,
        ...(index === 0 ? (page.active_rows ?? []) : []),
        ...(index === 0 ? (page.starred_rows ?? []) : []),
      ].some((row) => row.thread.thread_id === thread.thread_id),
    );
    if (observed.has(thread.thread_id) && observedAt <= (page?.observedAt ?? 0))
      continue;
    if (!belongs(thread)) {
      observed.delete(thread.thread_id);
      continue;
    }
    if (
      observed.has(thread.thread_id) ||
      results.tracker?.isUnread(thread.thread_id)
    )
      observed.set(thread.thread_id, {
        ...observed.get(thread.thread_id),
        thread,
      });
  }
  const selectedPage = selected
    ? list.data?.pages.find((page, index) =>
        (index === 0
          ? [
              ...page.rows,
              ...(page.active_rows ?? []),
              ...(page.starred_rows ?? []),
            ]
          : page.rows
        ).some((row) => row.thread.thread_id === selected.thread_id),
      )
    : undefined;
  if (
    selected &&
    (!observed.has(selected.thread_id) ||
      selectedUpdatedAt >
        Math.max(
          selectedPage?.observedAt ?? 0,
          results.threads.get(selected.thread_id)?.observedAt ?? 0,
        ))
  ) {
    observed.set(selected.thread_id, {
      ...observed.get(selected.thread_id),
      thread: selected,
    });
  }
  const ownerIds = [
    ...new Set(
      [...observed.values()]
        .filter(
          (row) => !row.thread.archived && row.thread.coordinator_thread_id,
        )
        .map((row) => row.thread.coordinator_thread_id!),
    ),
  ];
  const owners = useThreadOwners(
    ownerIds.filter((id) => !observed.has(id)),
    enabled && expanded,
  );
  for (const thread of owners.data ?? []) {
    if (
      belongs(thread) &&
      ownerIds.includes(thread.thread_id) &&
      !observed.has(thread.thread_id)
    )
      observed.set(thread.thread_id, { thread });
  }
  // Use the same reconciled observations for grouping and activity summaries.
  // Worker disclosure queries are lazy and must not reset these counts.
  const activeWorkerCounts = new Map<string, number>();
  for (const { thread } of observed.values()) {
    if (
      !thread.archived &&
      thread.role === "worker" &&
      thread.coordinator_thread_id &&
      thread.root_activity.state !== "inactive"
    ) {
      const owner = thread.coordinator_thread_id;
      activeWorkerCounts.set(owner, (activeWorkerCounts.get(owner) ?? 0) + 1);
    }
  }
  const activeRows: Row[] = [];
  const unreadRows: Row[] = [];
  const recentRows: Row[] = [];
  let unreadCount = 0;
  for (const row of observed.values()) {
    if (row.thread.archived && !ownerIds.includes(row.thread.thread_id))
      continue;
    const unread = results.tracker?.isUnread(row.thread.thread_id);
    if (unread) unreadCount++;
    if (row.thread.role === "worker") continue;
    (row.thread.root_activity.state !== "inactive" ||
    (row.thread.role === "coordinator" &&
      activeWorkerCounts.has(row.thread.thread_id))
      ? activeRows
      : unread
        ? unreadRows
        : recentRows
    ).push(row);
  }
  const byTouch = (a: Row, b: Row) => {
    const left = a.thread.touched_at ?? a.thread.created_at;
    const right = b.thread.touched_at ?? b.thread.created_at;
    return left && right
      ? right.localeCompare(left) ||
          b.thread.thread_id.localeCompare(a.thread.thread_id)
      : 0;
  };
  activeRows.sort(byTouch);
  unreadRows.sort(byTouch);
  recentRows.sort(
    (a, b) =>
      Number(!!b.thread.starred) - Number(!!a.thread.starred) || byTouch(a, b),
  );
  const rows = [...activeRows, ...unreadRows, ...recentRows];
  const markAllRead = async () => {
    if (!results.tracker || markingRead) return;
    // Capture the displayed group's versions before storage writes can yield.
    const unread = [...observed.values()]
      .filter(({ thread }) => !thread.archived)
      .map(({ thread }) => thread);
    setMarkingRead(true);
    try {
      await results.tracker.acknowledgeAll(unread);
    } finally {
      setMarkingRead(false);
    }
  };
  return (
    <section
      hidden={hidden}
      data-project-key={group.projectId}
      aria-label={group.name}
      className={`${styles.projectGroup} ${order.moving === group.id ? styles.movingProject : ""}`}
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
          <span title={group.name}>{group.name}</span>
          {unreadCount > 0 && (
            <small
              className={styles.resultCount}
              aria-label={`${unreadCount} conversations with new results`}
              title="Conversations with new results"
            >
              {unreadCount}
            </small>
          )}
        </button>
        <div className={styles.groupActions}>
          {expanded && list.isFetching && !!list.data && (
            <span
              role="status"
              className={styles.srOnly}
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
        <ErrorNotice error={owners.error} retry={() => void owners.refetch()} />
        <div>
          {rows.map((row, index) => (
            <Fragment key={row.thread.thread_id}>
              {activeRows.length > 0 && index === 0 && (
                <div
                  className={styles.sectionHeading}
                  role="heading"
                  aria-level={3}
                  aria-label={`Running · ${activeRows.length}`}
                >
                  <span>Running</span>
                  <small>{activeRows.length}</small>
                </div>
              )}
              {unreadRows.length > 0 && index === activeRows.length && (
                <div
                  className={styles.sectionHeading}
                  role="heading"
                  aria-level={3}
                  aria-label={`New results · ${unreadRows.length}`}
                >
                  <span>New results</span>
                  <small>{unreadRows.length}</small>
                  <Button
                    variant="ghost"
                    size="xs"
                    loading={markingRead}
                    aria-label={`Mark all results read in ${group.name}`}
                    title="Mark all current results in this group as read"
                    onClick={() => void markAllRead()}
                  >
                    Mark all read
                  </Button>
                </div>
              )}
              {recentRows.length > 0 &&
                index === activeRows.length + unreadRows.length && (
                  <div
                    className={styles.sectionHeading}
                    role="heading"
                    aria-level={3}
                  >
                    Recent
                  </div>
                )}
              {row.thread.role === "coordinator" ? (
                <CoordinatorEntry
                  row={row}
                  activeWorkerCount={
                    activeWorkerCounts.get(row.thread.thread_id) ?? 0
                  }
                  enabled={enabled && expanded}
                  selected={selected}
                  selectedUpdatedAt={selectedUpdatedAt}
                />
              ) : (
                <ThreadRow row={row} treeRow />
              )}
            </Fragment>
          ))}
          {expanded && !rows.length && !list.data && list.isPending && (
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
              size="xs"
              className={styles.showMore}
              loading={list.isFetchingNextPage}
              onClick={() => void list.fetchNextPage()}
              aria-label={`Show more conversations in ${group.name}`}
              title={`Show more conversations in ${group.name}`}
            >
              Show more
            </Button>
          )}
        </div>
      </div>
    </section>
  );
}

function SearchResults({
  query,
  project,
}: {
  query: string;
  project?: { project_id: string; name: string };
}) {
  const list = useThreads(query, project?.project_id);
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
          <small className={styles.emptyGroup}>
            {row.project_name}
            {row.thread.coordinator_thread_id ? " · Coordinator worker" : ""}
          </small>
          <ThreadRow row={row} />
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

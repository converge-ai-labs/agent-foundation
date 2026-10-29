import { Fragment, useEffect, useId, useRef, useState } from "react";
import { SharePanel } from "../sharing";
import { NavLink, useMatch, useNavigate } from "react-router";
import {
  Button,
  Menu,
  MenuTrigger,
  MenuPopup,
  MenuItem,
  Tooltip,
  TooltipTrigger,
  TooltipPopup,
} from "a13n-ui";
import {
  Plus,
  ChartBar,
  ShareNetwork,
  ArrowCounterClockwise,
  Folder,
  CaretRight,
  DotsThree,
  DotsSixVertical,
  Gear,
  ArrowUp,
  ArrowDown,
  PencilSimpleIcon,
  Check,
  ChatCircle,
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
import { ChatSearch } from "./chat-search";

type Group = {
  id: string;
  name: string;
  projectId?: string;
  roots?: string[];
  scope?: "projectless" | "unavailable";
};

export function ConversationNavigation() {
  const projects = useProjects();
  const navigate = useNavigate();
  const match = useMatch("/threads/:threadId");
  const selectedId = match?.params.threadId ?? "";
  const selectedDetail = useThread(selectedId);
  const selected = selectedDetail.data?.thread;
  const [adding, setAdding] = useState(false);
  const [renaming, setRenaming] = useState<Group | null>(null);
  const [expanded, setExpanded] = useProjectExpansion();
  const order = useProjectOrder(
    (projects.data ?? []).map((project) => project.project_id),
  );
  const groups: Group[] = order.ids.map((id) => {
    const project = projects.data!.find((item) => item.project_id === id)!;
    return { id, projectId: id, name: project.name, roots: project.roots };
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
  return (
    <div className={styles.navigation}>
      <nav aria-label="Chat shortcuts" className={styles.shortcuts}>
        <NavLink to="/" end className={styles.newConversationLink}>
          <Plus size={18} aria-hidden="true" />
          <span>New</span>
        </NavLink>
        <ChatSearch onOpen={order.cancel} />
        <DraftNavigation />
      </nav>
      <div className={styles.navigationHeading}>
        <span>Projects</span>
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
        <div>
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
  selected,
  selectedUpdatedAt,
  create,
  rename,
  order,
}: {
  group: Group;
  expanded: boolean;
  toggle: (open: boolean) => void;
  selected?: Schema<"ThreadSummary">;
  selectedUpdatedAt: number;
  create: () => void;
  rename: () => void;
  order: ReturnType<typeof useProjectOrder>;
}) {
  const navigate = useNavigate();
  const results = useResults();
  const [markingRead, setMarkingRead] = useState(false);
  const [sharing, setSharing] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const summaryId = useId();
  const headingRef = useRef<HTMLDivElement>(null);
  const showMoreRef = useRef<HTMLButtonElement>(null);
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
    enabled: expanded,
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
    expanded,
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
  const unreadOwners = new Set(
    [...observed.values()]
      .filter(
        ({ thread }) =>
          !thread.archived && results.tracker?.isUnread(thread.thread_id),
      )
      .map(({ thread }) => thread.coordinator_thread_id)
      .filter(Boolean),
  );
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
      : unread || unreadOwners.has(row.thread.thread_id)
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
  const selectedRootId = selected?.coordinator_thread_id ?? selected?.thread_id;
  const recentStart = activeRows.length + unreadRows.length;
  const isVisible = (row: Row, index: number) =>
    index < recentStart ||
    row.thread.thread_id === selectedRootId ||
    showAll ||
    index < recentStart + 5;
  const hasHiddenRows = rows.some((row, index) => !isVisible(row, index));
  const showMore = () => {
    setShowAll(true);
    if (!hasHiddenRows) void list.fetchNextPage();
  };
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
      data-project-key={group.projectId}
      aria-label={group.name}
      className={`${styles.projectGroup} ${order.moving === group.id ? styles.movingProject : ""}`}
    >
      <div ref={headingRef} className={styles.groupHeading}>
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
        <Tooltip>
          <TooltipTrigger
            disabled={!group.projectId}
            aria-describedby={group.projectId ? summaryId : undefined}
            className={styles.groupToggle}
            aria-expanded={expanded}
            onClick={() => toggle(!expanded)}
          >
            <Folder
              size={18}
              aria-hidden="true"
              className={styles.projectIcon}
            />
            <span>{group.name}</span>
            {unreadCount > 0 && (
              <small
                className={styles.resultCount}
                aria-label={`${unreadCount} chats with new results`}
                title="Chats with new results"
              >
                {unreadCount}
              </small>
            )}
            <span className={styles.groupChevron} aria-hidden="true">
              <CaretRight
                className={expanded ? styles.expandedChevron : undefined}
              />
            </span>
          </TooltipTrigger>
          {group.projectId && (
            <TooltipPopup
              id={summaryId}
              role="tooltip"
              className={styles.projectPreview}
              anchor={headingRef}
              side="right"
              align="start"
              sideOffset={12}
            >
              <div className={styles.projectSummary}>
                <strong>
                  <Folder size={18} />
                  {group.name}
                </strong>
                {list.data && (
                  <p>
                    <ChatCircle size={16} />
                    {list.data.pages[0].total} chats
                  </p>
                )}
                {(activeRows.length > 0 || unreadCount > 0) && (
                  <p className={styles.projectSummaryActivity}>
                    {[
                      activeRows.length > 0 && `${activeRows.length} active`,
                      unreadCount > 0 && `${unreadCount} with new results`,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </p>
                )}
                {!!group.roots?.length && (
                  <div className={styles.projectPaths}>
                    {group.roots.map((root) => (
                      <p key={root}>
                        <Folder size={16} />
                        <span>{root}</span>
                      </p>
                    ))}
                  </div>
                )}
              </div>
            </TooltipPopup>
          )}
        </Tooltip>
        <div className={styles.groupActions}>
          {expanded && list.isFetching && !!list.data && (
            <span
              role="status"
              className={styles.srOnly}
              aria-label={`Updating chats in ${group.name}`}
            >
              Updating…
            </span>
          )}
          {group.scope !== "unavailable" && (
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={`New chat in ${group.name}`}
              onClick={create}
            >
              <Plus />
            </Button>
          )}
          {(group.projectId || unreadCount > 0) && (
            <Menu>
              <MenuTrigger
                render={<Button variant="ghost" size="icon-sm" />}
                aria-label={`Actions for ${group.name}`}
              >
                <DotsThree />
              </MenuTrigger>
              <MenuPopup align="start" side="right">
                <MenuItem
                  onClick={() =>
                    navigate(
                      group.projectId
                        ? `/usage?project_id=${encodeURIComponent(group.projectId)}`
                        : "/usage?projectless=true",
                    )
                  }
                >
                  <ChartBar />
                  Usage
                </MenuItem>
                {unreadCount > 0 && (
                  <MenuItem
                    aria-label={`Mark all results read in ${group.name}`}
                    disabled={markingRead}
                    onClick={() => void markAllRead()}
                  >
                    <Check />
                    Mark all read
                  </MenuItem>
                )}
                {group.projectId && (
                  <>
                    <MenuItem onClick={() => setSharing(true)}>
                      <ShareNetwork />
                      Share
                    </MenuItem>
                    <MenuItem onClick={rename}>
                      <PencilSimpleIcon />
                      Rename
                    </MenuItem>
                    <MenuItem
                      onClick={() =>
                        navigate(
                          `/projects/${encodeURIComponent(group.projectId!)}`,
                        )
                      }
                    >
                      <Gear />
                      Settings
                    </MenuItem>
                    <MenuItem
                      disabled={order.ids.indexOf(group.id) === 0}
                      onClick={() => order.moveBy(group.id, -1)}
                    >
                      <ArrowUp />
                      Move up
                    </MenuItem>
                    <MenuItem
                      disabled={
                        order.ids.indexOf(group.id) === order.ids.length - 1
                      }
                      onClick={() => order.moveBy(group.id, 1)}
                    >
                      <ArrowDown />
                      Move down
                    </MenuItem>
                    <MenuItem onClick={order.reset}>
                      <ArrowCounterClockwise />
                      Reset order
                    </MenuItem>
                  </>
                )}
              </MenuPopup>
            </Menu>
          )}
        </div>
      </div>
      {group.projectId && (
        <SharePanel
          open={sharing}
          onClose={() => setSharing(false)}
          projectId={group.projectId}
        />
      )}
      <div hidden={!expanded} className={styles.groupThreads}>
        <ErrorNotice error={owners.error} retry={() => void owners.refetch()} />
        <div>
          {rows.map((row, index) => (
            <Fragment key={row.thread.thread_id}>
              {((index === 0 && activeRows.length > 0) ||
                (index === recentStart && recentStart > 0)) && (
                <div className={styles.sectionHeading}>
                  <span role="heading" aria-level={3}>
                    {index === 0 ? "Active" : "History"}
                  </span>
                </div>
              )}
              {unreadRows.length > 0 && index === activeRows.length && (
                <div className={styles.sectionHeading}>
                  <span
                    role="heading"
                    aria-level={3}
                    aria-label={`New results · ${unreadRows.length}`}
                  >
                    New results <small>{unreadRows.length}</small>
                  </span>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    loading={markingRead}
                    aria-label={`Mark all results read in ${group.name}`}
                    title="Mark all current results in this group as read"
                    onClick={() => void markAllRead()}
                  >
                    <Check />
                  </Button>
                </div>
              )}
              <div hidden={!isVisible(row, index)}>
                {row.thread.role === "coordinator" ? (
                  <CoordinatorEntry
                    row={row}
                    activeWorkerCount={
                      activeWorkerCounts.get(row.thread.thread_id) ?? 0
                    }
                    enabled={expanded && isVisible(row, index)}
                    selected={selected}
                    selectedUpdatedAt={selectedUpdatedAt}
                  />
                ) : (
                  <ThreadRow row={row} />
                )}
              </div>
            </Fragment>
          ))}
          {expanded && !rows.length && !list.data && list.isPending && (
            <div
              role="status"
              aria-label="Loading chats"
              aria-busy="true"
              className={styles.initialLoading}
            >
              <span>Loading chats…</span>
            </div>
          )}
          {list.isSuccess && !list.isPreviousData && !rows.length && (
            <small className={styles.emptyGroup}>No chats yet</small>
          )}
          <ErrorNotice error={list.error} retry={() => void list.refetch()} />
          {(hasHiddenRows || list.hasNextPage) && (
            <Button
              variant="ghost"
              size="xs"
              className={styles.showMore}
              loading={list.isFetchingNextPage}
              ref={showMoreRef}
              onClick={showMore}
              aria-label={`Show more chats in ${group.name}`}
              title={`Show more chats in ${group.name}`}
            >
              Show more
            </Button>
          )}
          {showAll && recentRows.length > 5 && (
            <Button
              variant="ghost"
              size="xs"
              className={styles.showMore}
              onClick={() => {
                setShowAll(false);
                requestAnimationFrame(() => showMoreRef.current?.focus());
              }}
              aria-label={`Show fewer chats in ${group.name}`}
            >
              Show less
            </Button>
          )}
        </div>
      </div>
    </section>
  );
}

import { createContext, useContext, useId, useState } from "react";
import {
  useInfiniteQuery,
  useIsMutating,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { Link, NavLink } from "react-router";
import { Button, SegmentedControl, Spinner } from "a13n-ui";
import {
  ArrowCounterClockwise,
  ArrowUpRight,
  Check,
  CheckCircle,
  Checks,
  EnvelopeSimple,
  EnvelopeSimpleOpen,
  Flag,
  HandWaving,
  Tray,
} from "@phosphor-icons/react";
import { useTranslation } from "./i18n";
import { result, type Schema } from "./transport/client";
import {
  TransportContext,
  useProjects,
  useTransport,
} from "./transport/context";
import { MessageText } from "./conversations/message-text";
import { markdownPreview } from "./conversations/markdown-preview";
import { describeTool, type ToolView } from "./conversations/tool-presentation";
import { ErrorNotice, PageHeader } from "./shell/ui";
import { inboxTarget } from "./inbox-target";
import row from "./shell/navigation-row.module.css";
import styles from "./inbox.module.css";

type Item = Schema<"InboxItem">;
type Kind = Item["kind"];
type Group = Schema<"InboxMarkReadRequest">["group"];
const UPDATE_GROUP = ["inbox", "update-group"];
const UPDATE_ITEM = ["inbox", "update-item"];

function useKind(kind: Kind) {
  const { t } = useTranslation();
  if (kind === "needs_human")
    return { Icon: HandWaving, label: t("Needs you") };
  if (kind === "milestone") return { Icon: Flag, label: t("Milestone") };
  return { Icon: CheckCircle, label: t("Result") };
}

function KindLabel({ kind }: { kind: Kind }) {
  const { Icon, label } = useKind(kind);
  return (
    <span className={styles.kind} data-kind={kind}>
      <Icon aria-hidden="true" />
      {label}
    </span>
  );
}

/** Long Markdown stays clamped until the reader asks for all of it. */
function ClampedMessage({ text }: { text: string }) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const long = text.length > 280 || text.split("\n").length > 5;
  return (
    <>
      <div
        className={styles.message}
        data-clamped={(long && !expanded) || undefined}
      >
        <MessageText text={text} />
      </div>
      {long && (
        <button
          type="button"
          className={styles.more}
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? t("Show less") : t("Show more")}
        </button>
      )}
    </>
  );
}

function useInboxUnread() {
  const { client, shareId } = useTransport();
  return useQuery({
    queryKey: ["inbox", "unread"],
    enabled: !shareId,
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/inbox", { params: { query: { limit: 1 } }, signal }),
      ),
    select: (page) => page.unread_count,
  });
}

function useInboxUpdate(group: Group) {
  const { client } = useTransport();
  const queries = useQueryClient();
  return useMutation({
    mutationKey: [...UPDATE_ITEM, group],
    mutationFn: ({
      itemId,
      ...body
    }: { itemId: string } & Schema<"InboxItemMutation">) =>
      result(
        client.PATCH("/api/inbox/{item_id}", {
          params: { path: { item_id: itemId } },
          body,
        }),
      ),
    onSettled: () => queries.invalidateQueries({ queryKey: ["inbox"] }),
  });
}

export function InboxNavigation() {
  const { t } = useTranslation();
  const unread = useInboxUnread().data ?? 0;
  return (
    <NavLink
      to="/inbox"
      className={`${row.row} ${styles.navigation}`}
      aria-label={
        unread
          ? t("Inbox, {{count, number}} unread", { count: unread })
          : undefined
      }
    >
      <Tray aria-hidden="true" />
      <span>{t("Inbox")}</span>
      {unread > 0 && (
        <span className={styles.count} aria-hidden="true">
          {t("{{count, number}}", { count: unread })}
        </span>
      )}
    </NavLink>
  );
}

/** Today shows the time; older items show the day. */
function shortTime(date: Date, locale: string) {
  const now = new Date();
  if (date.toDateString() === now.toDateString())
    return date.toLocaleTimeString(locale, {
      hour: "numeric",
      minute: "2-digit",
    });
  return date.toLocaleDateString(locale, {
    month: "short",
    day: "numeric",
    ...(date.getFullYear() !== now.getFullYear() && { year: "numeric" }),
  });
}

/** One scannable row; opening it reads the full message in place. */
function InboxRow({
  item,
  expanded,
  onExpand,
}: {
  item: Item;
  expanded: boolean;
  onExpand: (expanded: boolean) => void;
}) {
  const { t, i18n } = useTranslation();
  const projects = useProjects();
  const group = item.kind === "needs_human" ? "needs_you" : "updates";
  const update = useInboxUpdate(group);
  const completingGroup =
    useIsMutating({
      mutationKey: [...UPDATE_GROUP, group],
      predicate: ({ state }) => state.variables === "done",
    }) > 0;
  const { Icon, label } = useKind(item.kind);
  const unread = !item.read_at && !item.done_at;
  const done = !!item.done_at;
  const moving = update.isPending && update.variables?.done !== undefined;
  const target = inboxTarget(item.thread_id, item.item_id);
  const created = new Date(item.created_at);
  const source =
    item.thread_id === "thread_bot"
      ? t("Bot")
      : item.thread_title || t("Untitled chat");
  const project = item.project_id
    ? (projects.data?.find((entry) => entry.project_id === item.project_id)
        ?.name ?? t("Unavailable project"))
    : undefined;
  const context = expanded ? project : markdownPreview(item.message);
  const detail = `inbox-${item.item_id}`;
  const previewId = `inbox-preview-${item.item_id}`;
  const setRead = (read: boolean) =>
    update.mutate({ itemId: item.item_id, read });
  const setDone = (done: boolean) =>
    update.mutate({ itemId: item.item_id, done });
  const readLabel = unread ? t("Mark as read") : t("Mark as unread");
  const doneLabel = done ? t("Move back to Open") : t("Mark as done");
  return (
    <li
      className={styles.item}
      data-unread={unread || undefined}
      data-expanded={expanded || undefined}
      data-moving={moving || undefined}
    >
      <button
        type="button"
        className={styles.summary}
        aria-expanded={expanded}
        aria-controls={expanded ? detail : undefined}
        aria-label={[label, item.title, unread && t("Unread")]
          .filter(Boolean)
          .join(", ")}
        aria-describedby={previewId}
        onClick={() => {
          onExpand(!expanded);
          if (!expanded && unread) setRead(true);
        }}
      >
        <Icon
          className={styles.kindIcon}
          data-kind={item.kind}
          aria-hidden="true"
        />
        <span className={styles.title}>{item.title}</span>
        <time
          className={styles.time}
          dateTime={item.created_at}
          title={created.toLocaleString(i18n.language)}
        >
          {shortTime(created, i18n.language)}
        </time>
        <span className={styles.unread} aria-hidden="true" />
        <span id={previewId} className={styles.preview}>
          <span className={styles.source}>{source}</span>
          {context && ` · ${context}`}
        </span>
      </button>
      {!expanded && (
        <div className={styles.quick}>
          {!done && (
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label={readLabel}
              title={readLabel}
              disabled={update.isPending || (!unread && completingGroup)}
              onClick={() => setRead(unread)}
            >
              {unread ? <EnvelopeSimpleOpen /> : <EnvelopeSimple />}
            </Button>
          )}
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={t("Open in chat")}
            title={t("Open in chat")}
            render={<Link to={target} />}
          >
            <ArrowUpRight />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={doneLabel}
            title={doneLabel}
            disabled={update.isPending}
            onClick={() => setDone(!done)}
          >
            {done ? <ArrowCounterClockwise /> : <Check />}
          </Button>
        </div>
      )}
      {expanded && (
        <div id={detail} className={styles.detail}>
          <div className={styles.message}>
            <MessageText text={item.message} />
          </div>
          <div className={styles.actions}>
            <Button size="sm" render={<Link to={target} />}>
              {t("Open in chat")}
              <ArrowUpRight aria-hidden="true" />
            </Button>
            <Button
              variant="ghost"
              size="sm"
              loading={moving}
              disabled={update.isPending}
              onClick={() => setDone(!done)}
            >
              {done ? (
                <ArrowCounterClockwise aria-hidden="true" />
              ) : (
                <Check aria-hidden="true" />
              )}
              {doneLabel}
            </Button>
            {!done && (
              <Button
                variant="ghost"
                size="sm"
                disabled={update.isPending || completingGroup}
                onClick={() => {
                  setRead(false);
                  onExpand(false);
                }}
              >
                <EnvelopeSimple aria-hidden="true" />
                {t("Mark as unread")}
              </Button>
            )}
          </div>
        </div>
      )}
      <ErrorNotice error={update.error} inline />
    </li>
  );
}

function InboxGroupHeader({
  group,
  title,
  unread,
  fetching,
}: {
  group: Group;
  title: string;
  unread: number;
  fetching: boolean;
}) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const queries = useQueryClient();
  const heading = useId();
  const updatingItem =
    useIsMutating({ mutationKey: [...UPDATE_ITEM, group] }) > 0;
  const update = useMutation({
    mutationKey: [...UPDATE_GROUP, group],
    mutationFn: (action: "read" | "done") =>
      client.POST(
        action === "read" ? "/api/inbox/mark-read" : "/api/inbox/mark-done",
        {
          body: { group },
        },
      ),
    onSettled: () => queries.invalidateQueries({ queryKey: ["inbox"] }),
  });
  const actions = [
    { action: "read", label: t("Mark all as read"), Icon: EnvelopeSimpleOpen },
    { action: "done", label: t("Mark all as done"), Icon: Checks },
  ] as const;
  return (
    <>
      <header className={styles.groupHeader}>
        <div className={styles.groupLabel}>
          <h2 id={heading} className={styles.heading}>
            {title}
          </h2>
          <span className={styles.groupCount} role="status">
            {unread
              ? t("{{count, number}} unread", { count: unread })
              : t("All read")}
          </span>
        </div>
        <div className={styles.groupActions}>
          {actions.map(({ action, label, Icon }) => {
            const busy = update.isPending && update.variables === action;
            const unavailable =
              fetching ||
              update.isPending ||
              (action === "read" ? !unread : updatingItem);
            return (
              <Button
                key={action}
                variant="ghost"
                size="icon"
                aria-label={label}
                aria-describedby={heading}
                aria-busy={busy || undefined}
                aria-disabled={unavailable || undefined}
                tabIndex={unavailable ? -1 : undefined}
                onClick={() => {
                  if (!unavailable) update.mutate(action);
                }}
              >
                {busy ? (
                  <Spinner
                    aria-hidden="true"
                    className="motion-reduce:animate-none"
                  />
                ) : (
                  <Icon aria-hidden="true" />
                )}
              </Button>
            );
          })}
        </div>
      </header>
      <ErrorNotice
        error={update.error}
        message={
          update.variables === "done"
            ? t("Could not mark this section as done.")
            : t("Could not mark this section as read.")
        }
        inline
      />
    </>
  );
}

export function InboxPage() {
  const { t } = useTranslation();
  const { client } = useTransport();
  const [state, setState] = useState<"open" | "done">("open");
  const [expanded, setExpanded] = useState<string | null>(null);
  const updatingGroup = useIsMutating({ mutationKey: UPDATE_GROUP }) > 0;
  const list = useInfiniteQuery({
    queryKey: ["inbox", "list", state],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) =>
      result(
        client.GET("/api/inbox", {
          params: { query: { state, before: pageParam, limit: 30 } },
          signal,
        }),
      ),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const items = list.data?.pages.flatMap((page) => page.items) ?? [];
  const counts = list.data?.pages[0]?.unread_by_group;
  const groups = [
    {
      group: "needs_you",
      title: t("Needs you"),
      items: items.filter((item) => item.kind === "needs_human"),
    },
    {
      group: "updates",
      title: t("Updates"),
      items: items.filter((item) => item.kind !== "needs_human"),
    },
  ] as const;
  const rows = (entries: Item[]) => (
    <ul className={styles.items}>
      {entries.map((item) => (
        <InboxRow
          key={item.item_id}
          item={item}
          expanded={expanded === item.item_id}
          onExpand={(open) => setExpanded(open ? item.item_id : null)}
        />
      ))}
    </ul>
  );
  return (
    <section className={styles.page} aria-label={t("Inbox")}>
      <PageHeader
        title={t("Inbox")}
        actions={
          <SegmentedControl
            label={t("Inbox filter")}
            value={state}
            onValueChange={(value) => {
              if (value === "open" || value === "done") setState(value);
            }}
            options={[
              { value: "open", label: t("Open", { context: "inbox" }) },
              { value: "done", label: t("Done", { context: "inbox" }) },
            ]}
          />
        }
      />
      <ErrorNotice error={list.error} retry={() => void list.refetch()} />
      {list.isPending && <p role="status">{t("Loading Inbox…")}</p>}
      {list.isSuccess &&
        !items.length &&
        (state === "done" || !list.data.pages[0]?.unread_count) && (
          <div className={styles.empty}>
            <Tray aria-hidden="true" />
            <h2>
              {state === "open" ? t("Inbox is clear") : t("Nothing done yet")}
            </h2>
            <p>
              {state === "open"
                ? t(
                    "Agents post final results, questions for you, and real milestones here.",
                  )
                : t("Items you mark as done appear here.")}
            </p>
          </div>
        )}
      {state === "done"
        ? items.length > 0 && rows(items)
        : counts &&
          groups.map(({ group, title, items: entries }) => {
            const unread = counts[group];
            if (!entries.length && !unread) return null;
            return (
              <section key={group} className={styles.group} aria-label={title}>
                <InboxGroupHeader
                  group={group}
                  title={title}
                  unread={unread}
                  fetching={list.isFetching}
                />
                {entries.length ? (
                  rows(entries)
                ) : (
                  <p className={styles.laterPages}>
                    {t("Unread items in this section are on later pages.")}
                  </p>
                )}
              </section>
            );
          })}
      {list.hasNextPage && (
        <Button
          variant="outline"
          loading={list.isFetchingNextPage}
          disabled={list.isFetching || (state === "open" && updatingGroup)}
          onClick={() => void list.fetchNextPage({ cancelRefetch: false })}
        >
          {t("Load more")}
        </Button>
      )}
    </section>
  );
}

export const isInboxPost = (tool: ToolView) =>
  tool.name === "post_to_inbox" &&
  (!tool.provider || tool.provider === "function");

export const UnreadInboxPosts = createContext<ReadonlySet<string> | undefined>(
  undefined,
);

/** The transcript receipt for one post_to_inbox call; Inbox jumps land here. */
export function InboxPost({ tool }: { tool: ToolView }) {
  const { t } = useTranslation();
  const shareId = useContext(TransportContext)?.shareId;
  const unreadPosts = useContext(UnreadInboxPosts);
  const unread = tool.toolCallId && unreadPosts?.has(tool.toolCallId);
  const { args, phase } = describeTool(tool);
  const source = (
    <>
      <Tray aria-hidden="true" />
      {phase === "Result received"
        ? t("Sent to Inbox")
        : phase === "Receiving input" || phase === "Awaiting result"
          ? t("Sending to Inbox…")
          : t("Not sent to Inbox")}
    </>
  );
  const kind: Kind =
    args.kind === "needs_human" || args.kind === "milestone"
      ? args.kind
      : "result";
  return (
    <section
      className={styles.post}
      data-inbox-call={tool.toolCallId}
      tabIndex={-1}
      aria-label={t("Inbox post")}
    >
      <header className={styles.postHeader}>
        {shareId ? (
          <span className={styles.postSource}>{source}</span>
        ) : (
          <Link to="/inbox" className={styles.postSource}>
            {source}
            {unread && (
              <span
                className={styles.postUnread}
                role="img"
                aria-label={t("Unread")}
              />
            )}
          </Link>
        )}
        <KindLabel kind={kind} />
      </header>
      {typeof args.title === "string" && args.title && (
        <strong className={styles.postTitle}>{args.title}</strong>
      )}
      {typeof args.message === "string" && args.message && (
        <ClampedMessage text={args.message} />
      )}
    </section>
  );
}

/** Briefly mark the post a jump landed on. */
export function flashInboxPost(element: HTMLElement) {
  element.dataset.flash = "";
  window.setTimeout(() => delete element.dataset.flash, 2400);
}

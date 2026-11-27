import {
  createContext,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { useTransport, useGuestAccess } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { BOT_THREAD_ID } from "../bot-identity";
import {
  threadLinksLists,
  useThreadLinks,
  type ThreadLinkActions,
} from "../native/thread-links";
import { useLiveThread } from "./live-threads";
import { useChildExecutions } from "./details";
import { savedTools, type ToolView } from "./tool-presentation";
import { useHistory, useThread, transcriptHistory } from "./queries";
import { useTranslation } from "../i18n";
import {
  backgroundItems,
  type BackgroundItem,
  type BackgroundSection,
  type BackgroundTarget,
} from "./background-work";
import { readPreference, writePreference } from "../shell/preferences";
import type { FocusDisplay } from "./stream";

type BackgroundThreadLinks = {
  enabled: boolean;
  worktrees: Schema<"ThreadLinksView">["worktrees"];
  urls: Schema<"ThreadLinksView">["urls"];
  isPending: boolean;
  error: unknown;
  refetch: () => void;
  actions?: ThreadLinkActions;
};

type BackgroundState = {
  threadId: string;
  items: BackgroundItem[];
  resolve: (executionId: string) => BackgroundTarget | undefined;
  display?: FocusDisplay;
  open: (target: BackgroundTarget) => void;
  cardOpen: boolean;
  more: Record<BackgroundSection, boolean>;
  loadingMore: Record<BackgroundSection, boolean>;
  loadMore: (section: BackgroundSection) => void;
  setCardOpen: (open: boolean) => void;
  loading: boolean;
  error: unknown;
  refresh: () => void;
  threadLinks: BackgroundThreadLinks;
};
const EMPTY_THREAD_LINKS: BackgroundThreadLinks = {
  enabled: false,
  worktrees: [],
  urls: [],
  isPending: false,
  error: null,
  refetch: () => {},
};
const Context = createContext<BackgroundState>({
  threadId: "",
  items: [],
  resolve: () => undefined,
  open: () => {},
  cardOpen: false,
  more: { subagents: false, tools: false },
  loadingMore: { subagents: false, tools: false },
  loadMore: () => {},
  setCardOpen: () => {},
  loading: false,
  error: null,
  refresh: () => {},
  threadLinks: EMPTY_THREAD_LINKS,
});
export const useBackground = () => useContext(Context);
type BackgroundScopeProps = {
  threadId: string;
  observe?: boolean;
  closeOnOpen?: boolean;
  linkActions?: ThreadLinkActions;
  onOpen: BackgroundState["open"];
  children: ReactNode;
};

export function BackgroundScope(props: BackgroundScopeProps) {
  return props.threadId ? <ThreadBackgroundScope {...props} /> : props.children;
}

function ThreadBackgroundScope({
  threadId,
  onOpen,
  children,
  observe = false,
  closeOnOpen = false,
  linkActions,
}: BackgroundScopeProps) {
  const { t } = useTranslation();
  const { client, shareId } = useTransport();
  const guest = useGuestAccess();
  const queries = useQueryClient();
  const { display, revision } = useLiveThread(threadId, observe);
  const detail = useThread(threadId);
  const linksEnabled =
    !!threadId &&
    threadId !== BOT_THREAD_ID &&
    !guest &&
    !shareId &&
    !detail.data?.thread.memory_scope;
  const linksQuery = useThreadLinks(linksEnabled ? threadId : undefined);
  const { worktrees, urls } = threadLinksLists(linksQuery.data);
  const history = useHistory(
    threadId,
    detail.data?.continuation_id,
    !!threadId && !!detail.data,
  );
  // One latest execution per child conversation, running first, then newest
  // first; Show more requests older conversations.
  const childPage = useChildExecutions(threadId, {
    enabled: !!threadId,
    latest: true,
  });
  const toolPage = useInfiniteQuery({
    queryKey: ["thread", threadId, "tool-executions", "background"],
    enabled: !!threadId && !guest,
    initialPageParam: 0,
    queryFn: ({ pageParam, signal }) =>
      result(
        client.GET("/api/threads/{thread_id}/tool-executions", {
          params: {
            path: { thread_id: threadId },
            query: { offset: pageParam, limit: 100 },
          },
          signal,
        }),
      ),
    getNextPageParam: (page, _pages, offset) =>
      page.length === 100 ? offset + 100 : undefined,
  });
  const items = useMemo(() => {
    const calls: ToolView[] = [
      ...savedTools(transcriptHistory(history.data?.pages).entries).values(),
    ].filter((tool) => tool !== null);
    for (const block of display.blocks.values())
      if (block.kind === "tool")
        calls.push({ ...block, name: block.name ?? "", input: block.text });
    return backgroundItems(
      threadId,
      childPage.data?.pages.flatMap((page) => page.executions) ?? [],
      toolPage.data?.pages.flat() ?? [],
      display.processes.background,
      t,
      calls,
    );
  }, [
    threadId,
    childPage.data,
    toolPage.data,
    history.data,
    display,
    revision,
    t,
  ]);
  const [cardStates, setCardStates] = useState<Record<string, boolean>>({});
  const cardOpen =
    !!threadId &&
    (cardStates[threadId] ??
      readPreference(`background.${threadId}`, "closed") === "open");
  const setCardOpen = (value: boolean) => {
    setCardStates((current) => ({ ...current, [threadId]: value }));
    writePreference(`background.${threadId}`, value ? "open" : "closed");
  };
  const wrapLinkAction = (callback?: (path: string) => void) =>
    callback
      ? (path: string) => {
          callback(path);
          if (closeOnOpen) setCardOpen(false);
        }
      : undefined;
  const wrappedLinkActions: ThreadLinkActions | undefined = linkActions
    ? {
        openChanges: wrapLinkAction(linkActions.openChanges),
        openFiles: wrapLinkAction(linkActions.openFiles),
        openTerminal: wrapLinkAction(linkActions.openTerminal),
      }
    : undefined;
  const refresh = () => {
    void queries.invalidateQueries({
      queryKey: ["thread", threadId, "children"],
    });
    void queries.invalidateQueries({
      queryKey: ["thread", threadId, "tool-executions"],
    });
    if (linksEnabled)
      void queries.invalidateQueries({
        queryKey: ["thread-links", threadId],
      });
  };
  return (
    <Context
      value={{
        threadId,
        items,
        resolve: (id) => {
          const child = childPage.data?.pages
            .flatMap((page) => page.executions)
            .find((child) => child.execution_id === id);
          return child
            ? {
                kind: "child",
                id: child.child_thread_id,
                threadId,
                title: child.subagent_name,
              }
            : items.find((item) => item.target.id === id)?.target;
        },
        display,
        open: (target) => {
          onOpen(target);
          if (closeOnOpen) setCardOpen(false);
        },
        cardOpen,
        more: {
          subagents: !!childPage.hasNextPage,
          tools: !!toolPage.hasNextPage,
        },
        loadingMore: {
          subagents: childPage.isFetchingNextPage,
          tools: toolPage.isFetchingNextPage,
        },
        loadMore: (section) =>
          void (section === "subagents"
            ? childPage.fetchNextPage()
            : toolPage.fetchNextPage()),
        setCardOpen,
        refresh,
        loading:
          !!threadId && (childPage.isPending || (!guest && toolPage.isPending)),
        error: childPage.error ?? toolPage.error,
        threadLinks: {
          enabled: linksEnabled,
          worktrees,
          urls,
          isPending: linksEnabled && linksQuery.isPending,
          error: linksEnabled ? linksQuery.error : null,
          refetch: () => void linksQuery.refetch(),
          actions: wrappedLinkActions,
        },
      }}
    >
      {children}
    </Context>
  );
}

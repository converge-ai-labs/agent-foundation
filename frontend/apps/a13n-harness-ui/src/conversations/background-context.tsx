import {
  createContext,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { useTransport, useGuestAccess } from "../transport/context";
import { result } from "../transport/client";
import { useLiveThread } from "./live-threads";
import { useChildExecutions } from "./details";
import { savedTools, type ToolView } from "./tool-presentation";
import { useHistory, useThread, transcriptHistory } from "./queries";
import { useTranslation } from "../i18n";
import {
  backgroundItems,
  type BackgroundItem,
  type BackgroundTarget,
} from "./background-work";
import { readPreference, writePreference } from "../shell/preferences";
import type { FocusDisplay } from "./stream";

type BackgroundState = {
  items: BackgroundItem[];
  resolve: (executionId: string) => BackgroundTarget | undefined;
  display?: FocusDisplay;
  open: (target: BackgroundTarget) => void;
  cardOpen: boolean;
  more: boolean;
  loadingMore: boolean;
  loadMore: () => void;
  setCardOpen: (open: boolean) => void;
  loading: boolean;
  error: unknown;
  refresh: () => void;
};
const Context = createContext<BackgroundState>({
  items: [],
  resolve: () => undefined,
  open: () => {},
  cardOpen: false,
  more: false,
  loadingMore: false,
  loadMore: () => {},
  setCardOpen: () => {},
  loading: false,
  error: null,
  refresh: () => {},
});
export const useBackground = () => useContext(Context);
type BackgroundScopeProps = {
  threadId: string;
  observe?: boolean;
  closeOnOpen?: boolean;
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
}: BackgroundScopeProps) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const guest = useGuestAccess();
  const queries = useQueryClient();
  const { display, revision } = useLiveThread(threadId, observe);
  const detail = useThread(threadId);
  const history = useHistory(
    threadId,
    detail.data?.continuation_id,
    !!threadId && !!detail.data,
  );
  // Read a bounded status page; Show more is the only way to request older work.
  const childPage = useChildExecutions(threadId, !!threadId);
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
  const refresh = () => {
    void queries.invalidateQueries({
      queryKey: ["thread", threadId, "children"],
    });
    void queries.invalidateQueries({
      queryKey: ["thread", threadId, "tool-executions"],
    });
  };
  return (
    <Context
      value={{
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
        more: !!(childPage.hasNextPage || toolPage.hasNextPage),
        loadingMore:
          childPage.isFetchingNextPage || toolPage.isFetchingNextPage,
        loadMore: () => {
          if (childPage.hasNextPage) void childPage.fetchNextPage();
          if (toolPage.hasNextPage) void toolPage.fetchNextPage();
        },
        setCardOpen,
        refresh,
        loading:
          !!threadId && (childPage.isPending || (!guest && toolPage.isPending)),
        error: childPage.error ?? toolPage.error,
      }}
    >
      {children}
    </Context>
  );
}

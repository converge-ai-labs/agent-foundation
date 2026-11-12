import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { useTransport, useGuestAccess } from "../transport/context";
import { result } from "../transport/client";
import { useLiveThread } from "./live-threads";
import { useChildExecutions } from "./details";
import type { ToolView } from "./tool-presentation";
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
  anchor?: { right: number; bottom: number };
  setAnchor?: (anchor: { right: number; bottom: number }) => void;
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
  setCardOpen: () => {},
  loading: false,
  error: null,
  refresh: () => {},
});
export const useBackground = () => useContext(Context);
export function BackgroundScope({
  threadId,
  onOpen,
  children,
  observe = false,
}: {
  threadId: string;
  observe?: boolean;
  onOpen: BackgroundState["open"];
  children: ReactNode;
}) {
  const { client } = useTransport();
  const guest = useGuestAccess();
  const queries = useQueryClient();
  const { display, revision } = useLiveThread(threadId, observe);
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
  useEffect(() => {
    if (
      childPage.hasNextPage &&
      !childPage.isFetching &&
      !childPage.isFetchNextPageError
    )
      void childPage.fetchNextPage();
  }, [childPage.hasNextPage, childPage.isFetching, childPage.fetchNextPage]);
  useEffect(() => {
    if (
      toolPage.hasNextPage &&
      !toolPage.isFetching &&
      !toolPage.isFetchNextPageError
    )
      void toolPage.fetchNextPage();
  }, [toolPage.hasNextPage, toolPage.isFetching, toolPage.fetchNextPage]);
  const items = useMemo(() => {
    const calls: ToolView[] = [];
    for (const block of display.blocks.values())
      if (block.kind === "tool")
        calls.push({ ...block, name: block.name ?? "", input: block.text });
    return backgroundItems(
      threadId,
      childPage.data?.pages.flatMap((page) => page.executions) ?? [],
      toolPage.data?.pages.flat() ?? [],
      display.processes.background,
      calls,
    );
  }, [threadId, childPage.data, toolPage.data, display, revision]);
  const [anchor, updateAnchor] = useState<{ right: number; bottom: number }>();
  const setAnchor = (next: { right: number; bottom: number }) =>
    updateAnchor((previous) =>
      previous?.right === next.right && previous.bottom === next.bottom
        ? previous
        : next,
    );
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
        open: onOpen,
        cardOpen,
        anchor,
        setAnchor,
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

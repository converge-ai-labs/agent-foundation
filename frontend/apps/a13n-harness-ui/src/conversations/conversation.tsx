import { useConnectorOffers } from "../connectors/offers";
import { MessageBranchProvider } from "./message-branch";
import { ReaderFades } from "./reader-fades";
import { useComposerRunChoices } from "./composer-run-choices";
import { useTranslation } from "../i18n";
import {
  type ComponentProps,
  type ReactNode,
  useContext,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  Link,
  Navigate,
  useLocation,
  useParams,
  useSearchParams,
} from "react-router";
import { BOT_THREAD_ID } from "../bot-identity";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  ModalFrame,
  Tooltip,
  TooltipTrigger,
  TooltipPopup,
} from "a13n-ui";
import { ArrowDown, Sparkle } from "@phosphor-icons/react";
import { result, type Schema } from "../transport/client";
import {
  useGuestAccess,
  useSelectors,
  useTransport,
} from "../transport/context";
import { SharePanel } from "../sharing";
import coordinatorStyles from "./coordinator.module.css";
import { ErrorNotice, TextField } from "../shell/ui";
import type { Profile } from "../shell/presence";
import { readPreference, writePreference } from "../shell/preferences";
import { Composer, submitContinuation, useDraft } from "./composer";
import type { DictationTarget } from "./dictation-context";
import { useDebug } from "../shell/debug";
import {
  AppContextProvider,
  useAppContextSelection,
} from "../mcp-apps/context-selection";
import { ComposerStatus } from "./composer-status";
import { currentGoal } from "./goal-status";
import { RunActivity } from "./run-activity";
import { RunEnvironments, ThreadRunChoices } from "./thread-run-choices";
import { Decisions, useDecisionPlacement } from "./decisions";
import { McpInputs, useMcpInputRequests } from "./mcp-inputs";
import { ConversationDetails } from "./details";
import {
  canPromoteCoordinator,
  CoordinatorSettings,
} from "./coordinator-settings";
import { WorkInspector } from "./work-inspector";
import { RootFailureNotice } from "./failure-notice";
import {
  transcriptHistory,
  useHistory,
  useOperation,
  useThread,
} from "./queries";
import { refreshThread } from "./refresh";
import { refreshActivity } from "./activity-updates";
import { applyThreadMutation } from "./thread-updates";
import { showFocusedOutput } from "./stream";
import { useLiveThread } from "./live-threads";
import { ConnectionNoticeContext } from "../shell/connection";
import {
  ConversationUnavailable,
  isUnavailableChat,
} from "./conversation-unavailable";
import { LiveConnectionStatus } from "./live-connection";
import { ConversationTranscript, type PublishedMessage } from "./transcript";
import { PauseConversationFollowing } from "./execution-details";
import { inputSource, conversationTitle } from "./local-input";
import { InputNavigation } from "./input-navigation";
import {
  captureReadingAnchor,
  restoreReadingAnchor,
  type ReadingAnchor,
} from "./reading-anchor";
import styles from "./conversation.module.css";
import layout from "../shell/workbench.module.css";
import { useResults } from "./results";
import { savedResultVisible } from "./result-visibility";
import { ReplyAnnotations } from "./reply-annotations";
import { ConversationOpening, useInitialReady } from "./opening";
import { canDeleteThread, DeleteThread } from "./delete-thread";

export function ConversationPage(props: {
  profile: Profile;
  unauthorized: () => void;
}) {
  const { threadId = "" } = useParams();
  const location = useLocation();
  if (threadId === BOT_THREAD_ID)
    return (
      <Navigate
        to={{ pathname: "/bot", search: location.search, hash: location.hash }}
        replace
      />
    );
  return <Conversation key={threadId} threadId={threadId} {...props} />;
}
export function Conversation(
  props: ComponentProps<typeof ConversationContent>,
) {
  return (
    <AppContextProvider key={props.threadId}>
      <ConversationContent {...props} />
    </AppContextProvider>
  );
}
function ConversationContent({
  threadId,
  profile,
  unauthorized,
  publication,
}: {
  threadId: string;
  profile: Profile;
  unauthorized: () => void;
  publication?: {
    threadAvailable: boolean;
    ready: boolean;
    revision: string;
    completionVersion: number;
    messages: PublishedMessage[];
    activity?: ReactNode;
    composer:
      ReactNode | ((dictation: DictationTarget | undefined) => ReactNode);
    status: ReactNode;
  };
}) {
  const { t } = useTranslation();
  const transport = useTransport();
  const debug = useDebug();
  const disconnected = useContext(ConnectionNoticeContext);
  const appContexts = useAppContextSelection();
  const queries = useQueryClient();
  const detail = useThread(
    publication && !publication.threadAvailable ? "" : threadId,
  );
  const selectors = useSelectors();
  const guest = useGuestAccess();
  const mcpInputs = useMcpInputRequests(threadId, !guest && !!detail.data);
  const readOnly = !!detail.data?.thread.memory_scope;
  const connectorOffers = useConnectorOffers(
    threadId,
    !guest && !!detail.data && !readOnly,
  );
  const isBot = threadId === BOT_THREAD_ID;
  const canCompose =
    !isBot && !readOnly && (!guest || guest.grant.permission === "prompt");
  const isCoordinator = detail.data?.thread.role === "coordinator";
  const ownerId = detail.data?.thread.coordinator_thread_id;
  const owner = useQuery({
    queryKey: ["thread", ownerId, "detail"],
    enabled: !!ownerId && !guest,
    queryFn: ({ signal }) =>
      result(
        transport.client.GET("/api/threads/{thread_id}", {
          params: { path: { thread_id: ownerId! } },
          signal,
        }),
      ),
  });
  const results = useResults();
  const tracker = readOnly ? undefined : results.tracker;
  useEffect(() => {
    if (detail.data) tracker?.observe(detail.data.thread, detail.dataUpdatedAt);
  }, [detail.data, detail.dataUpdatedAt, tracker]);
  const agentSelection = useMutation({
    mutationFn: async (agentId: string) => {
      if (!detail.data)
        throw new Error("Refresh the chat before choosing an agent.");
      return result(
        transport.client.PATCH("/api/threads/{thread_id}/configuration", {
          params: { path: { thread_id: threadId } },
          body: {
            expected_version: detail.data.thread.configuration.version,
            patch: { agent_id: agentId },
          },
        }),
      );
    },
    onSuccess: (updated) => {
      applyThreadMutation(queries, updated);
    },
    onSettled: () => {
      refreshThread(queries, threadId, "configuration");
      refreshActivity(queries, transport, threadId);
    },
  });
  const [search, setSearch] = useSearchParams();
  const dialog = search.get("dialog");
  const closeDialog = () =>
    setSearch(
      (current) => {
        current.delete("dialog");
        return current;
      },
      { replace: true },
    );
  const draft = useDraft(threadId, true);
  const [selectedTurn, setSelectedTurn] = useState<string>();
  const pendingJump = useRef<string | undefined>(undefined);
  const history = useHistory(
    threadId,
    detail.data?.continuation_id,
    !!detail.data,
    selectedTurn,
  );
  const { display, connection, reconnections, revision } =
    useLiveThread(threadId);
  const [showAvailable, setShowAvailable] = useState(false);
  const pageReady = useInitialReady(
    publication?.ready ||
      showAvailable ||
      // A confirmed Send already has readable input. Keep it and the next
      // prompt visible while the destination observes detail and saved history.
      draft.localInputs.some((input) => input.state === "accepted") ||
      detail.isError ||
      !!detail.data?.thread.parent_thread_id ||
      (!!detail.data && (!!history.data || history.isError)),
    // Saved body readiness is independent of live replay and editor sync.
    // The composer retains its own connection/control guards before sending.
  );
  const rename = !isBot && !guest && !readOnly && dialog === "rename";
  const setRename = (open: boolean) => {
    if (!open) closeDialog();
  };
  const [deleting, setDeleting] = useState(false);
  const [inspectionActions, setInspectionActions] =
    useState<HTMLDivElement | null>(null);
  const inspection = !guest && dialog === "details";
  const setInspection = (open: boolean) => {
    if (!open) closeDialog();
  };
  const reader = useRef<HTMLDivElement>(null);
  const restoreScroll = useRef(readPreference(`scroll.${threadId}`, ""));
  const follow = useRef(true);
  const readingAnchor = useRef<ReadingAnchor | undefined>(undefined);
  const scrollFrame = useRef<number | null>(null);
  const stopScrolling = useCallback(() => {
    if (scrollFrame.current !== null) cancelAnimationFrame(scrollFrame.current);
    scrollFrame.current = null;
  }, []);
  const scrollToLatest = useCallback(
    (instant = false) => {
      const element = reader.current;
      if (!element) return;
      if (
        instant ||
        window.matchMedia("(prefers-reduced-motion: reduce)").matches
      ) {
        stopScrolling();
        element.scrollTop = element.scrollHeight;
        return;
      }
      if (scrollFrame.current !== null) return;
      let previous = performance.now();
      const step = (now: number) => {
        if (!follow.current) {
          scrollFrame.current = null;
          return;
        }
        const target = Math.max(0, element.scrollHeight - element.clientHeight);
        const distance = target - element.scrollTop;
        if (Math.abs(distance) < 1) {
          element.scrollTop = target;
          scrollFrame.current = null;
          return;
        }
        // Some browsers quantize scrollTop to whole pixels. Keep progressing
        // near the target instead of scheduling frames forever below one pixel.
        element.scrollTop +=
          Math.sign(distance) *
          Math.max(
            1,
            Math.abs(distance) *
              (1 - Math.exp(-Math.min(now - previous, 64) / 65)),
          );
        previous = now;
        scrollFrame.current = requestAnimationFrame(step);
      };
      scrollFrame.current = requestAnimationFrame(step);
    },
    [stopScrolling],
  );
  useEffect(() => stopScrolling, [stopScrolling]);
  const interruptScroll = () => {
    stopScrolling();
    follow.current = false;
    if (reader.current)
      readingAnchor.current = captureReadingAnchor(reader.current);
  };
  const olderAnchor = useRef<{ height: number; top: number } | null>(null);
  const [newOutput, setNewOutput] = useState(false);
  const backToLatest = () => {
    pendingJump.current = undefined;
    olderAnchor.current = null;
    readingAnchor.current = undefined;
    setSelectedTurn(undefined);
    follow.current = true;
    setNewOutput(false);
    scrollToLatest();
  };
  const reconcile = useCallback(() => {
    refreshThread(queries, threadId, "reconcile");
    refreshActivity(queries, transport, threadId);
    if (threadId === BOT_THREAD_ID)
      void queries.invalidateQueries({ queryKey: ["bot"] });
  }, [queries, transport, threadId]);
  const decisions = useDecisionPlacement(
    detail.data?.deferred_requests?.length ? (
      guest ? (
        <p role="status">{t("Waiting for the instance owner to respond.")}</p>
      ) : (
        <Decisions
          threadId={threadId}
          continuation={detail.data.continuation_id}
          reconcile={reconcile}
        />
      )
    ) : null,
  );
  const { entries, turns } = useMemo(
    () => transcriptHistory(history.data?.pages),
    [history.data],
  );
  const latestPage = history.data?.pages[0];
  const hasLater = !!(latestPage?.later_turns_cursor === undefined
    ? latestPage?.newer_cursor
    : latestPage.later_turns_cursor);
  const findTurn = useCallback(
    (id: string) =>
      [
        ...(reader.current?.querySelectorAll<HTMLElement>("[data-turn-id]") ??
          []),
      ].find((element) => element.dataset.turnId === id),
    [],
  );
  const jumpToInput = (id: string) => {
    interruptScroll();
    const target = findTurn(id);
    pendingJump.current = id;
    if (!target) setSelectedTurn(id);
    else {
      const element = reader.current!;
      element.scrollTop +=
        target.getBoundingClientRect().top -
        element.getBoundingClientRect().top -
        16;
      target.focus({ preventScroll: true });
      readingAnchor.current = captureReadingAnchor(element);
      pendingJump.current = undefined;
    }
  };
  useLayoutEffect(() => {
    if (!pendingJump.current || history.isPreviousHistory) return;
    const target = findTurn(pendingJump.current);
    const element = reader.current;
    if (!target || !element) return;
    element.scrollTop +=
      target.getBoundingClientRect().top -
      element.getBoundingClientRect().top -
      16;
    target.focus({ preventScroll: true });
    readingAnchor.current = captureReadingAnchor(element);
    pendingJump.current = undefined;
  }, [entries, history.isPreviousHistory, findTurn]);
  const reconcileSavedInputs = useCallback(
    (savedEntries: Schema<"TranscriptEntry">[]) => {
      const saved = new Set(
        savedEntries.flatMap((entry) => entry.parts.map(inputSource)),
      );
      const retained = draft.localInputs.filter(
        (input) => !saved.has(input.id),
      );
      if (retained.length !== draft.localInputs.length) {
        draft.localInputs = retained;
        draft.notify();
      }
    },
    [draft],
  );
  useEffect(
    () => reconcileSavedInputs(entries),
    [entries, reconcileSavedInputs],
  );
  const latestLocalInput = draft.localInputs.at(-1)?.id;
  const previousLocalInput = useRef(latestLocalInput);
  useLayoutEffect(() => {
    if (latestLocalInput && latestLocalInput !== previousLocalInput.current) {
      setSelectedTurn(undefined);
      pendingJump.current = undefined;
      follow.current = true;
      setNewOutput(false);
      scrollToLatest(true);
    }
    previousLocalInput.current = latestLocalInput;
  }, [latestLocalInput, scrollToLatest]);
  const continuation = history.data?.pages[0]?.continuation_id;
  const lastTurn = turns.at(-1);
  const completionVersion =
    publication?.completionVersion ??
    (history.isPreviousHistory || history.isError
      ? 0
      : (history.data?.pages[0]?.completion_version ?? 0));
  const readState = results.threads.get(threadId)?.thread;
  useEffect(() => {
    if (
      hasLater ||
      !pageReady ||
      !tracker ||
      (!publication &&
        (!history.data || history.isPreviousHistory || history.isError)) ||
      !readState?.unread ||
      (readState.completion?.version ?? 0) !== completionVersion
    )
      return;
    const element = reader.current;
    if (!element) return;
    const check = () => {
      if (!olderAnchor.current && savedResultVisible(element))
        void tracker.acknowledge(
          threadId,
          completionVersion,
          readState.read_state_version ?? 0,
        );
    };
    // The effect runs after transcript commit; the frame observes layout and the
    // actual scroll position, never the optimistic follow/smooth-scroll flag.
    const frame = requestAnimationFrame(check);
    const observer =
      typeof ResizeObserver === "undefined"
        ? undefined
        : new ResizeObserver(check);
    observer?.observe(element);
    if (element.firstElementChild) observer?.observe(element.firstElementChild);
    element.addEventListener("scroll", check);
    window.addEventListener("focus", check);
    window.addEventListener("resize", check);
    document.addEventListener("visibilitychange", check);
    return () => {
      cancelAnimationFrame(frame);
      observer?.disconnect();
      element.removeEventListener("scroll", check);
      window.removeEventListener("focus", check);
      window.removeEventListener("resize", check);
      document.removeEventListener("visibilitychange", check);
    };
  }, [
    pageReady,
    tracker,
    threadId,
    completionVersion,
    readState?.unread,
    readState?.read_state_version,
    readState?.completion?.version,
    history.isPreviousHistory,
    history.isError,
    hasLater,
    history.data,
    publication?.revision,
  ]);
  const operation = detail.data?.thread.root_activity;
  const [lastReceipt, setLastReceipt] = useState<string | null>(null);
  useEffect(() => {
    if (display.snapshot)
      setLastReceipt(
        display.snapshot.root_operation?.receipt.receipt_id ?? null,
      );
  }, [display.snapshot]);
  useEffect(() => {
    if (operation?.receipt_id) setLastReceipt(operation.receipt_id);
  }, [operation?.receipt_id]);
  const receipt =
    operation?.receipt_id ??
    lastReceipt ??
    (draft.submission.kind === "accepted"
      ? draft.submission.receipt
      : undefined);
  const observedOperation = useOperation(
    threadId,
    operation?.receipt_id ??
      (draft.submission.kind === "accepted"
        ? draft.submission.receipt
        : receipt),
  );
  // Advance only after the replacement history query arrives. SSE completion alone
  // is not evidence that continuation was saved.
  const presentation = display.presentationFor(continuation);
  const showLive =
    !hasLater &&
    showFocusedOutput(
      presentation,
      continuation,
      operation?.run_id,
      history.isPreviousHistory,
    );
  const liveBlocks = presentation.blocksAfter(continuation);
  const visibleContent = `${publication?.revision ?? ""}:${continuation}:${entries.length}:${draft.localInputs.map((input) => input.id).join(",")}:${
    showLive
      ? liveBlocks
          .filter(
            (block) => !block.diagnostic && block.metadata?.display !== false,
          )
          .map(
            (block) =>
              `${block.id}:${block.text.length}:${block.result?.length ?? 0}`,
          )
          .join("|")
      : ""
  }`;
  const lastContent = useRef("");
  useLayoutEffect(() => {
    const element = reader.current;
    if (!pageReady || !element || (!history.data && !publication)) return;
    const restored = !!restoreScroll.current;
    if (restoreScroll.current) {
      try {
        const saved = JSON.parse(restoreScroll.current);
        if (typeof saved.top === "number" && saved.follow === false) {
          element.scrollTop = saved.top;
          follow.current = false;
        }
      } catch {
        /* Ignore an invalid personal scroll preference. */
      }
      restoreScroll.current = "";
    }
    if (
      !follow.current &&
      !olderAnchor.current &&
      !restored &&
      !pendingJump.current
    )
      restoreReadingAnchor(element, readingAnchor.current);
    if (olderAnchor.current && !history.isFetchingNextPage) {
      element.scrollTop =
        olderAnchor.current.top +
        element.scrollHeight -
        olderAnchor.current.height;
      olderAnchor.current = null;
    } else if (follow.current) {
      // Streaming commits and late layout use one bottom-follow policy. Only
      // an explicit New output action animates; resize must not fight that RAF.
      if (scrollFrame.current === null) scrollToLatest(true);
    } else if (
      !restored &&
      !olderAnchor.current &&
      lastContent.current !== visibleContent
    )
      setNewOutput(true);
    readingAnchor.current = follow.current
      ? undefined
      : captureReadingAnchor(element);
    lastContent.current = visibleContent;
  }, [
    pageReady,
    revision,
    visibleContent,
    history.data,
    history.isFetchingNextPage,
    scrollToLatest,
  ]);
  useEffect(() => {
    const element = reader.current;
    const content = element?.firstElementChild;
    if (
      !pageReady ||
      !element ||
      !content ||
      typeof ResizeObserver === "undefined"
    )
      return;
    const observer = new ResizeObserver(() => {
      // Both late content and a growing composer can move the actual bottom.
      if (
        follow.current &&
        !olderAnchor.current &&
        scrollFrame.current === null
      )
        scrollToLatest(true);
      else if (!follow.current && !olderAnchor.current && !pendingJump.current)
        restoreReadingAnchor(element, readingAnchor.current);
      readingAnchor.current = follow.current
        ? undefined
        : captureReadingAnchor(element);
    });
    observer.observe(element);
    observer.observe(content);
    return () => observer.disconnect();
  }, [pageReady, scrollToLatest]);
  useEffect(() => {
    const element = reader.current;
    // Also retry the top-edge observation after an in-flight refetch settles.
    // Short/context-only pages need no scroll gesture to fill the viewport.
    // Turn-boundary cursors skip gaps with their own explicit message loader.
    if (
      pageReady &&
      element &&
      element.clientHeight > 0 &&
      element.scrollTop < 160 &&
      history.hasNextPage &&
      !history.isFetching &&
      !history.isFetchNextPageError &&
      !olderAnchor.current
    ) {
      olderAnchor.current = {
        height: element.scrollHeight,
        top: element.scrollTop,
      };
      void history.fetchNextPage();
    }
  }, [
    pageReady,
    history.data,
    history.hasNextPage,
    history.isFetching,
    history.isFetchNextPageError,
    history.fetchNextPage,
  ]);
  useLayoutEffect(() => {
    const element = reader.current;
    if (!pageReady) return;
    return () => {
      if (element)
        writePreference(
          `scroll.${threadId}`,
          JSON.stringify({ top: element.scrollTop, follow: follow.current }),
        );
    };
  }, [threadId, pageReady]);
  useEffect(() => {
    if (!guest && detail.data && !detail.data.thread?.archived)
      writePreference("last-thread", threadId);
  }, [threadId, detail.data, guest]);
  const metadata = useMutation({
    mutationFn: (patch: { title?: string | null; archived?: boolean }) =>
      result(
        transport.client.PATCH("/api/threads/{thread_id}/metadata", {
          params: { path: { thread_id: threadId } },
          body: {
            expected_version: detail.data!.thread.metadata_version,
            patch,
          },
        }),
      ),
    onSuccess: (updated) => {
      applyThreadMutation(queries, updated);
      setRename(false);
    },
    onSettled: () => {
      refreshThread(queries, threadId, "metadata");
      refreshActivity(queries, transport, threadId);
    },
  });
  const thread = detail.data?.thread;
  const runAgent = guest ? undefined : thread?.configuration.agent_source.id;
  const refreshRunConfiguration = useCallback(async () => {
    const [current, catalog] = await Promise.all([
      detail.refetch(),
      selectors.refetch(),
    ]);
    if (!current.isSuccess || !catalog.isSuccess) return;
    const configuration = current.data.thread.configuration;
    const agent = configuration.agent_source.id;
    return {
      agent,
      inheritedModel:
        configuration.default_model_id ??
        catalog.data.agents.find((item) => item.agent_id === agent)?.model_id ??
        null,
    };
  }, [detail.refetch, selectors.refetch]);
  const runChoices = useComposerRunChoices(
    draft,
    runAgent,
    thread?.configuration.default_model_id ??
      (selectors.data
        ? (selectors.data.agents.find((item) => item.agent_id === runAgent)
            ?.model_id ?? null)
        : undefined),
    refreshRunConfiguration,
    guest ? undefined : threadId,
  );
  const unavailable =
    !publication &&
    detail.isError &&
    (isUnavailableChat(detail.error) ||
      (!detail.data && !draft.localInputs.length && !showLive));
  const pageError =
    detail.error ||
    (!history.isFetchNextPageError ? history.error : null) ||
    agentSelection.error ||
    selectors.error ||
    mcpInputs.error ||
    (!rename && metadata.error);
  const feedback = pageError ? (
    <ErrorNotice
      inline
      error={pageError}
      retry={() => {
        if (history.error) void history.retryRead();
        else reconcile();
        if (selectors.error) void selectors.refetch();
        if (agentSelection.error || (!rename && metadata.error))
          void detail.refetch().then((updated) => {
            if (updated.isSuccess) {
              agentSelection.reset();
              metadata.reset();
            }
          });
      }}
    />
  ) : !!detail.data &&
    !disconnected &&
    history.isPending &&
    !history.data &&
    !showLive ? (
    <span role="status">{t("Loading messages\u2026")}</span>
  ) : null;
  const connectionStatus =
    !disconnected && !feedback ? (
      <LiveConnectionStatus
        connection={connection}
        reconnections={reconnections}
      />
    ) : null;
  if (unavailable)
    return (
      <ConversationUnavailable
        error={detail.error}
        retry={() => void detail.refetch()}
        retrying={detail.isFetching}
        hasDraft={
          !!draft.doc.getText("text").length ||
          !!draft.doc.getMap("attachments").size
        }
      />
    );

  const goal = currentGoal(thread, observedOperation.data);
  const showGoal = !!goal && !guest && !readOnly && !thread?.archived;
  if (thread?.parent_thread_id)
    return (
      <div>
        <h1>{t("Child chat")}</h1>
        <p>
          {t(
            "View this subagent from its parent chat. You can't send messages here.",
          )}
        </p>
        <Link to={`/threads/${encodeURIComponent(thread.parent_thread_id)}`}>
          {t("Open parent chat")}
        </Link>
      </div>
    );
  const failureNotice = (
    <RootFailureNotice
      lastExecution={thread?.last_execution}
      threadId={threadId}
      receipt={receipt}
      display={display}
      continuationId={detail.data?.continuation_id}
      completedContinuationId={thread?.completion?.continuation_id}
      retry={
        isBot || guest || readOnly || thread?.archived
          ? undefined
          : () => {
              void submitContinuation(
                draft,
                transport,
                threadId,
                undefined,
                () => appContexts?.capture(threadId) ?? [],
              ).finally(reconcile);
            }
      }
      retryDisabled={
        !detail.data?.available_actions?.includes("run") ||
        thread?.root_activity.state !== "inactive" ||
        agentSelection.isPending ||
        agentSelection.isError ||
        detail.isError ||
        draft.submission.kind === "pending" ||
        draft.submission.kind === "unknown" ||
        (draft.submission.kind === "accepted" &&
          draft.submission.receipt !== receipt)
      }
    />
  );
  const terminalTurn =
    thread?.root_activity.state === "inactive" &&
    (thread.last_execution?.status === "cancelled" ||
      thread.last_execution?.status === "failed") &&
    thread.last_execution.completed_at &&
    continuation === detail.data?.continuation_id &&
    lastTurn &&
    lastTurn.end_position === latestPage?.total &&
    lastTurn.final_position == null
      ? {
          id: lastTurn.turn_id,
          time: thread.last_execution.completed_at,
          notice: failureNotice,
        }
      : undefined;
  const dictation =
    !guest && selectors.data?.dictation
      ? {
          words: selectors.data.dictation.context_words ?? 2000,
          reader,
          title: thread?.title ?? "",
          threadId,
        }
      : undefined;
  const content = (
    <>
      <div className={styles.page}>
        {!guest && !readOnly && thread?.archived && (
          <div className={styles.warning}>
            <p>{t("This chat is archived. Its history remains available.")}</p>
            <Button
              variant="outline"
              loading={metadata.isPending}
              onClick={() => metadata.mutate({ archived: false })}
            >
              {t("Restore chat")}
            </Button>
          </div>
        )}
        <div className={styles.readerFrame}>
          <ReaderFades />
          {!publication && (
            <InputNavigation
              threadId={threadId}
              continuation={continuation}
              localInputs={draft.localInputs}
              reader={reader}
              revision={visibleContent}
              onSelect={jumpToInput}
            />
          )}
          <div
            ref={reader}
            className={`${styles.reading} a13n-scrollbar`}
            onWheel={(event) => {
              if (event.deltaY < 0) interruptScroll();
            }}
            onTouchStart={interruptScroll}
            onPointerDown={interruptScroll}
            onKeyDown={(event) => {
              if (["ArrowUp", "PageUp", "Home"].includes(event.key))
                interruptScroll();
            }}
            onScroll={() => {
              if (!pageReady) return;
              const element = reader.current!;
              // Layout-driven scroll events must not detach an active follower.
              // After a user gesture, resume only at the actual bottom.
              if (scrollFrame.current === null && !follow.current)
                follow.current =
                  !hasLater &&
                  element.scrollHeight -
                    element.scrollTop -
                    element.clientHeight <=
                    1;
              if (follow.current) setNewOutput(false);
              readingAnchor.current = follow.current
                ? undefined
                : captureReadingAnchor(element);
              if (
                element.scrollTop < 160 &&
                history.hasNextPage &&
                !history.isFetching &&
                !history.isFetchNextPageError &&
                !olderAnchor.current
              ) {
                olderAnchor.current = {
                  height: element.scrollHeight,
                  top: element.scrollTop,
                };
                void history.fetchNextPage();
              }
            }}
          >
            <div className={styles.transcript}>
              {history.isFetchingNextPage && (
                <small role="status">
                  {t("Loading earlier messages\u2026")}
                </small>
              )}
              {history.isFetchNextPageError && (
                <Button
                  variant="ghost"
                  onClick={() => {
                    const element = reader.current;
                    if (element)
                      olderAnchor.current = {
                        height: element.scrollHeight,
                        top: element.scrollTop,
                      };
                    void history.fetchNextPage();
                  }}
                >
                  {t("Retry loading")}
                </Button>
              )}
              <>
                <PauseConversationFollowing value={interruptScroll}>
                  <MessageBranchProvider
                    receipt={thread?.root_activity.receipt_id}
                    thread={threadId}
                    draft={draft}
                    continuation={continuation}
                    enabled={
                      !!thread &&
                      !isBot &&
                      !guest &&
                      thread.role === "ordinary" &&
                      !thread.parent_thread_id &&
                      !thread.memory_scope &&
                      thread.configuration.agent_source.kind === "agent" &&
                      !thread.archived
                    }
                    active={
                      thread?.root_activity.state !== "inactive" ||
                      draft.submission.kind === "pending"
                    }
                  >
                    <ConversationTranscript
                      publications={publication?.messages}
                      hasEarlier={history.hasNextPage}
                      hasLater={hasLater}
                      connectorOffers={
                        !guest ? connectorOffers.data : undefined
                      }
                      terminalTurn={terminalTurn}
                      currentFailureRunId={
                        thread?.last_execution?.status === "failed"
                          ? thread.last_execution.run_id
                          : undefined
                      }
                      entries={entries}
                      running={
                        operation?.state === "running" &&
                        presentation === display &&
                        operation.run_id === presentation.runId
                      }
                      blocks={showLive ? liveBlocks : []}
                      localInputs={hasLater ? [] : draft.localInputs}
                      turns={turns}
                      loadDetails
                      onSavedEntries={reconcileSavedInputs}
                      continuation={continuation}
                      gap={showLive && display.gap}
                      threadId={threadId}
                      activity={
                        publication?.activity ??
                        (!readOnly &&
                        !showGoal &&
                        !hasLater &&
                        !thread?.archived &&
                        !detail.data?.deferred_requests?.length ? (
                          <RunActivity
                            recovery={display.recovery}
                            activity={operation}
                            submission={draft.submission}
                            operation={observedOperation.data}
                          />
                        ) : undefined)
                      }
                      pending={
                        !hasLater && detail.data?.deferred_requests?.length
                          ? {
                              requestIds: detail.data.deferred_requests
                                .filter(
                                  (request) =>
                                    request.tool_name === "ask_user_question",
                                )
                                .map((request) => request.request_id),
                              content: decisions.slot,
                            }
                          : undefined
                      }
                    />
                    {decisions.portal}
                    {!guest && (
                      <McpInputs
                        threadId={threadId}
                        requests={mcpInputs.data}
                        reconcile={() => void mcpInputs.refetch()}
                      />
                    )}
                  </MessageBranchProvider>
                </PauseConversationFollowing>
                {hasLater && (
                  <div className={styles.historyActions}>
                    <Button
                      variant="ghost"
                      loading={history.isFetchingPreviousPage}
                      onClick={() => void history.fetchPreviousPage()}
                    >
                      {t("Load newer")}
                    </Button>
                    <Button variant="ghost" onClick={backToLatest}>
                      {t("Back to latest")}
                    </Button>
                  </div>
                )}
                {!terminalTurn && failureNotice}

                {((guest && !canCompose) || readOnly || isCoordinator) &&
                  !entries.length &&
                  !draft.localInputs.length &&
                  thread?.root_activity.state === "inactive" &&
                  !showLive &&
                  !history.isPending &&
                  !history.error && (
                    <div
                      className={`${styles.empty} ${isCoordinator ? coordinatorStyles.intro : ""}`}
                    >
                      {guest && !canCompose ? (
                        <>
                          <h2>{t("No messages yet")}</h2>
                          <p>
                            {t(
                              "Messages will appear here when this chat gets started.",
                            )}
                          </p>
                        </>
                      ) : readOnly ? (
                        <>
                          <h2>{t("No organization history yet")}</h2>
                          <p>
                            {t(
                              "Automatic memory updates appear here. Viewing this page won't start a run.",
                            )}
                          </p>
                        </>
                      ) : isCoordinator ? (
                        <>
                          <h2>{t("What are we working on?")}</h2>
                          <p>
                            {t(
                              "Share a goal. I’ll help plan the work and bring results back here.",
                            )}
                          </p>
                        </>
                      ) : null}
                    </div>
                  )}
              </>
            </div>
          </div>
        </div>
        {(newOutput ||
          (hasLater && !!detail.data?.deferred_requests?.length)) && (
          <Button
            className={styles.newOutput}
            variant="outline"
            onClick={backToLatest}
          >
            <ArrowDown />
            {detail.data?.deferred_requests?.length
              ? t("Response needed")
              : t("New output")}
          </Button>
        )}
        {!readOnly && detail.data && (
          <WorkInspector
            showDetails={!isBot || debug.enabled}
            threadId={threadId}
            connectionStatus={publication?.status ?? connectionStatus}
            status={
              !guest && thread && !thread.archived ? (
                <ComposerStatus
                  threadId={threadId}
                  receipt={receipt}
                  busy={thread.root_activity.state !== "inactive"}
                  liveRecordId={
                    showLive ? display.contextUsage?.recordId : undefined
                  }
                  liveTokens={
                    showLive ? display.contextUsage?.tokens : undefined
                  }
                />
              ) : undefined
            }
            connected={connection === "Live"}
            goal={showGoal ? goal : null}
            activity={thread?.root_activity}
            onUseGoal={
              goal &&
              thread?.root_activity.state === "inactive" &&
              detail.data?.available_actions?.includes("run") &&
              draft.submission.kind !== "pending" &&
              draft.submission.kind !== "unknown"
                ? () => {
                    const text = draft.doc.getText("text");
                    text.insert(
                      text.length,
                      `${text.length ? "\n\n" : ""}${goal.objective}`,
                    );
                    draft.mode = "goal";
                    draft.notify();
                  }
                : undefined
            }
          />
        )}
        {readOnly && connectionStatus && (
          <div className={styles.conversationFeedback}>{connectionStatus}</div>
        )}
        {(!canCompose || thread?.archived) && feedback && (
          <div className={styles.conversationFeedback}>{feedback}</div>
        )}
        {publication?.status && !detail.data && (
          <div className={styles.conversationFeedback}>
            {publication.status}
          </div>
        )}
        {typeof publication?.composer === "function"
          ? publication.composer(dictation)
          : publication?.composer}
        {canCompose && !thread?.archived && (
          <Composer
            dictation={dictation}
            feedback={feedback}
            feedbackError={pageError}
            suppressConnectionNotice={
              disconnected || connection !== "Live" || !!pageError
            }
            autoFocus={pageReady && search.get("compose") === "1"}
            threadId={threadId}
            activity={thread?.root_activity ?? { state: "inactive" }}
            owner={
              !guest && ownerId
                ? {
                    title: owner.data
                      ? conversationTitle(owner.data.thread)
                      : ownerId,
                  }
                : undefined
            }
            coordinator={
              !guest && thread && thread.role !== "worker"
                ? {
                    active: thread.role === "coordinator",
                    available: canPromoteCoordinator(
                      thread,
                      !!detail.data?.deferred_requests?.length,
                    ),
                  }
                : undefined
            }
            canRun={
              (!runAgent || !runChoices.pending) &&
              !agentSelection.isPending &&
              !agentSelection.isError &&
              !detail.isError &&
              (detail.data?.available_actions?.includes("run") ?? false)
            }
            continuationId={detail.data?.continuation_id}
            canClearContext={
              !detail.isError &&
              (detail.data?.available_actions?.includes("clear_context") ??
                false)
            }
            unavailableReason={
              agentSelection.isPending
                ? t("Updating chat settings…")
                : !detail.data
                  ? t("Loading chat…")
                  : undefined
            }
            leadingControls={
              guest ? undefined : thread?.configuration.agent_source.kind ===
                "live" ? (
                <Link to="/settings#live">{t("Live settings")}</Link>
              ) : (
                <RunEnvironments
                  catalog={selectors.data}
                  configuration={thread?.configuration}
                  value={draft.environment}
                  onChange={(value) => {
                    draft.environment = value;
                    draft.notify();
                  }}
                  disabled={
                    !thread ||
                    draft.submission.kind === "pending" ||
                    draft.submission.kind === "unknown"
                  }
                />
              )
            }
            controls={() =>
              guest ? null : thread?.configuration.agent_source.kind ===
                "live" ? null : (
                <ThreadRunChoices
                  catalog={selectors.data}
                  agentId={thread?.configuration.agent_source.id ?? ""}
                  defaultModelId={thread?.configuration.default_model_id}
                  modelId={draft.modelId}
                  controls={draft.controls}
                  onControlsChange={runChoices.onControlsChange}
                  disabled={
                    !thread ||
                    agentSelection.isPending ||
                    draft.submission.kind === "pending" ||
                    draft.submission.kind === "unknown"
                  }
                  onAgentChange={(value) => agentSelection.mutate(value)}
                  onModelChange={runChoices.onModelChange}
                />
              )
            }
            profile={profile}
            unauthorized={unauthorized}
            reconcile={reconcile}
          />
        )}
        {guest && !canCompose && (
          <p className={styles.composerConnection}>
            {t("View-only link - Following this chat live.")}
          </p>
        )}
        {!guest && !readOnly && !isBot && (
          <SharePanel
            open={dialog === "share"}
            onClose={closeDialog}
            threadId={threadId}
            projectId={thread?.configuration.project_id}
          />
        )}
        <ModalFrame
          open={rename}
          onOpenChange={setRename}
          title={t("Rename chat")}
          closeLabel={t("Close")}
        >
          {rename && thread && (
            <RenameChatForm
              key={threadId}
              threadId={threadId}
              initialTitle={thread.title ?? ""}
              modelId={draft.modelId}
              saving={metadata.isPending}
              error={metadata.error}
              onCancel={() => setRename(false)}
              onSave={(title) => metadata.mutate({ title: title || null })}
            />
          )}
        </ModalFrame>
        <ModalFrame
          open={inspection}
          onOpenChange={setInspection}
          title={t("Chat details")}
          closeLabel={t("Close dialog")}
          footer={
            <>
              <Button variant="outline" onClick={() => setInspection(false)}>
                {t("Close")}
              </Button>
              <div
                ref={setInspectionActions}
                className={styles.settingsActions}
              />
            </>
          }
          size="lg"
        >
          <ConversationDetails
            actionsContainer={inspectionActions}
            threadId={threadId}
            receipt={receipt}
            continuation={detail.data?.continuation_id}
            reconcile={reconcile}
            management={
              <div className={styles.detailActions}>
                {!isBot && !readOnly && thread && (
                  <CoordinatorSettings
                    thread={thread}
                    pending={!!detail.data?.deferred_requests?.length}
                  />
                )}
                {!thread?.archived &&
                  detail.data?.available_actions?.includes("archive") && (
                    <Button
                      variant="outline"
                      loading={metadata.isPending}
                      onClick={() => metadata.mutate({ archived: true })}
                    >
                      {t("Archive chat")}
                    </Button>
                  )}
                {!guest && thread && canDeleteThread(thread) && (
                  <Button
                    variant="destructive-outline"
                    disabled={thread.root_activity.state !== "inactive"}
                    onClick={() => {
                      setInspection(false);
                      setDeleting(true);
                    }}
                  >
                    {t("Delete chat")}
                  </Button>
                )}
              </div>
            }
            readOnly={readOnly}
          />
        </ModalFrame>
      </div>
      {deleting && thread && (
        <DeleteThread thread={thread} close={() => setDeleting(false)} />
      )}
    </>
  );
  return (
    <ConversationOpening
      ready={pageReady}
      label={t("Opening chat…")}
      onContinue={() => setShowAvailable(true)}
    >
      <ReplyAnnotations
        dictation={dictation}
        reader={reader}
        draft={draft}
        enabled={canCompose && !thread?.archived}
      >
        {content}
      </ReplyAnnotations>
    </ConversationOpening>
  );
}

function RenameChatForm({
  threadId,
  initialTitle,
  modelId,
  saving,
  error,
  onCancel,
  onSave,
}: {
  threadId: string;
  initialTitle: string;
  modelId?: string;
  saving: boolean;
  error: unknown;
  onCancel: () => void;
  onSave: (title: string) => void;
}) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const [title, setTitle] = useState(initialTitle);
  const suggestion = useMutation({
    mutationFn: () =>
      result(
        client.POST("/api/threads/{thread_id}/title-suggestion", {
          params: {
            path: { thread_id: threadId },
            query: { model_id: modelId },
          },
        }),
      ),
    onSuccess: setTitle,
  });
  return (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        onSave(title);
      }}
    >
      <TextField
        label={t("Title")}
        value={title}
        disabled={suggestion.isPending || saving}
        onChange={(value) => setTitle(value.slice(0, 512))}
      />
      <ErrorNotice error={suggestion.error || error} />
      <div className={layout.dialogActions} data-a13n-form-actions>
        <Tooltip>
          <TooltipTrigger
            render={
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                loading={suggestion.isPending}
                disabled={saving}
              />
            }
            aria-label={t("Generate title")}
            onClick={() => suggestion.mutate()}
          >
            <Sparkle aria-hidden="true" />
          </TooltipTrigger>
          <TooltipPopup>{t("Generate title")}</TooltipPopup>
        </Tooltip>
        <Button
          type="button"
          variant="outline"
          disabled={saving}
          onClick={onCancel}
        >
          {t("Cancel")}
        </Button>
        <Button type="submit" loading={saving} disabled={suggestion.isPending}>
          {t("Save title")}
        </Button>
      </div>
    </form>
  );
}

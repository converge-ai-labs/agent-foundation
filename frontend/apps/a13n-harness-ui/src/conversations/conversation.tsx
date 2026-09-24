import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { ArrowDown } from "@phosphor-icons/react";
import { result, type Schema } from "../transport/client";
import { useSelectors, useTransport } from "../transport/context";
import coordinatorStyles from "./coordinator.module.css";
import { ErrorNotice, TextField } from "../shell/ui";
import type { Profile } from "../shell/presence";
import { readPreference, writePreference } from "../shell/preferences";
import { Composer, submitContinuation, useDraft } from "./composer";
import { ComposerStatus } from "./composer-status";
import { RunEnvironments, ThreadRunChoices } from "./thread-run-choices";
import { Decisions } from "./decisions";
import { ConversationDetails } from "./details";
import { CoordinatorSettings } from "./coordinator-settings";
import { WorkInspector } from "./work-inspector";
import { RootFailureNotice } from "./failure-notice";
import { refreshThreadLists, useHistory, useThread } from "./queries";
import { showFocusedOutput } from "./stream";
import { useLiveThread } from "./live-threads";
import { LiveConnectionNotice } from "./live-connection";
import { ConversationTranscript, RecoveryNotice } from "./transcript";
import { PauseConversationFollowing } from "./execution-details";
import { inputSource } from "./local-input";
import { InputNavigation } from "./input-navigation";
import {
  captureReadingAnchor,
  restoreReadingAnchor,
  type ReadingAnchor,
} from "./reading-anchor";
import styles from "./conversation.module.css";
import { useResults } from "./results";
import { savedResultVisible } from "./result-visibility";
import { ConversationOpening, useInitialReady } from "./opening";

export function ConversationPage(props: {
  profile: Profile;
  unauthorized: () => void;
}) {
  const { threadId = "" } = useParams();
  return <Conversation key={threadId} threadId={threadId} {...props} />;
}
function Conversation({
  threadId,
  profile,
  unauthorized,
}: {
  threadId: string;
  profile: Profile;
  unauthorized: () => void;
}) {
  const transport = useTransport();
  const queries = useQueryClient();
  const detail = useThread(threadId);
  const selectors = useSelectors();
  const isCoordinator = detail.data?.thread.role === "coordinator";
  const results = useResults();
  const tracker = results.tracker;
  useEffect(() => {
    if (detail.data)
      void tracker?.follow(detail.data.thread, detail.dataUpdatedAt);
  }, [detail.data, detail.dataUpdatedAt, tracker]);
  const agentSelection = useMutation({
    mutationFn: async (agentId: string) => {
      if (!detail.data)
        throw new Error("Refresh the conversation before choosing an agent.");
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
      draft.controls = {};
      draft.notify();
      queries.setQueryData<Schema<"ThreadDetail">>(
        ["thread", threadId, "detail"],
        (current) => {
          if (
            !current ||
            current.thread.configuration.version > updated.configuration.version
          )
            return current;
          return {
            ...current,
            thread: { ...current.thread, configuration: updated.configuration },
          };
        },
      );
    },
    onSettled: () =>
      queries.invalidateQueries({ queryKey: ["thread", threadId] }),
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
  const draft = useDraft(threadId);
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
    showAvailable ||
      detail.isError ||
      !!detail.data?.thread.parent_thread_id ||
      (!!detail.data && (!!history.data || history.isError)),
    // Saved body readiness is independent of live replay and editor sync.
    // The composer retains its own connection/control guards before sending.
  );
  const rename = dialog === "rename";
  const setRename = (open: boolean) => {
    if (!open) closeDialog();
  };
  const [title, setTitle] = useState("");
  const inspection = dialog === "details";
  const setInspection = (open: boolean) => {
    if (!open) closeDialog();
  };
  useEffect(() => {
    if (rename) setTitle(detail.data?.thread.title ?? "");
  }, [rename, detail.data?.thread.title]);
  const [message, setMessage] = useState("");
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
    void queries.invalidateQueries({ queryKey: ["thread", threadId] });
    void refreshThreadLists(queries);
    void queries.invalidateQueries({ queryKey: ["child-saved-output"] });
  }, [queries, threadId]);
  const entries = useMemo(() => {
    const byPosition = new Map<number, Schema<"TranscriptEntry">>();
    for (const page of history.data?.pages ?? [])
      for (const entry of [...(page.boundary_entries ?? []), ...page.entries])
        byPosition.set(entry.position, entry);
    return [...byPosition.values()].sort((a, b) => a.position - b.position);
  }, [history.data]);
  const turns = useMemo(() => {
    const byId = new Map<string, Schema<"TranscriptTurn">>();
    for (const page of history.data?.pages ?? [])
      for (const turn of page.turns ?? []) byId.set(turn.turn_id, turn);
    return [...byId.values()].sort(
      (a, b) => a.input_position - b.input_position,
    );
  }, [history.data]);
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
  const completionVersion =
    history.isPreviousHistory || history.isError
      ? 0
      : (history.data?.pages[0]?.completion_version ?? 0);
  const acknowledged = results.followed.get(threadId);
  useEffect(() => {
    if (
      hasLater ||
      !pageReady ||
      !tracker ||
      !completionVersion ||
      acknowledged === undefined ||
      acknowledged >= completionVersion
    )
      return;
    const element = reader.current;
    if (!element) return;
    const check = () => {
      if (!olderAnchor.current && savedResultVisible(element))
        void tracker.acknowledge(threadId, completionVersion);
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
    acknowledged,
    hasLater,
    history.data,
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
  const visibleContent = `${continuation}:${entries.length}:${draft.localInputs.map((input) => input.id).join(",")}:${
    showLive
      ? liveBlocks
          .filter((block) => !block.diagnostic)
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
    if (!pageReady || !element || !history.data) return;
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
    readingAnchor.current = captureReadingAnchor(element);
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
      readingAnchor.current = captureReadingAnchor(element);
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
    if (detail.data && !detail.data.thread?.archived)
      writePreference("last-thread", threadId);
  }, [threadId, detail.data]);
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
    onSuccess: () => {
      setRename(false);
      reconcile();
    },
    onError: reconcile,
  });
  const thread = detail.data?.thread;
  if (thread?.parent_thread_id)
    return (
      <div>
        <h1>Child conversation</h1>
        <p>
          Inspect child execution through its parent; it does not have a root
          composer.
        </p>
        <Link to={`/threads/${encodeURIComponent(thread.parent_thread_id)}`}>
          Open parent conversation
        </Link>
      </div>
    );
  return (
    <ConversationOpening
      ready={pageReady}
      label="Opening conversation…"
      onContinue={() => setShowAvailable(true)}
    >
      <div className={styles.page}>
        <LiveConnectionNotice
          connection={connection}
          reconnections={reconnections}
        />

        {thread?.archived && (
          <div className={styles.warning}>
            <p>This conversation is archived. Its history remains available.</p>
            <Button
              variant="outline"
              loading={metadata.isPending}
              onClick={() => metadata.mutate({ archived: false })}
            >
              Restore conversation
            </Button>
          </div>
        )}
        {message && (
          <p role="status" className={styles.receipt}>
            {message}
          </p>
        )}
        <ErrorNotice
          error={detail.error || history.error || metadata.error}
          retry={reconcile}
        />
        <div className={styles.readerFrame}>
          <InputNavigation
            threadId={threadId}
            continuation={continuation}
            localInputs={draft.localInputs}
            reader={reader}
            revision={visibleContent}
            onSelect={jumpToInput}
          />
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
              readingAnchor.current = captureReadingAnchor(element);
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
                <small role="status">Loading earlier messages…</small>
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
                  Retry earlier messages
                </Button>
              )}
              <PauseConversationFollowing value={interruptScroll}>
                <ConversationTranscript
                  entries={entries}
                  blocks={showLive ? liveBlocks : []}
                  localInputs={hasLater ? [] : draft.localInputs}
                  turns={turns}
                  loadDetails
                  onSavedEntries={reconcileSavedInputs}
                  continuation={continuation}
                  gap={showLive && display.gap}
                  threadId={threadId}
                  recovery={
                    !hasLater && display.recovery?.state === "resumed"
                      ? display.recovery
                      : undefined
                  }
                />
              </PauseConversationFollowing>
              {hasLater && (
                <div className={styles.historyActions}>
                  <Button
                    variant="ghost"
                    loading={history.isFetchingPreviousPage}
                    onClick={() => void history.fetchPreviousPage()}
                  >
                    Load later messages
                  </Button>
                  <Button variant="ghost" onClick={backToLatest}>
                    Back to latest
                  </Button>
                </div>
              )}
              <RootFailureNotice
                threadId={threadId}
                receipt={receipt}
                display={display}
                continuationId={detail.data?.continuation_id}
                completedContinuationId={thread?.completion?.continuation_id}
                retry={
                  thread?.archived
                    ? undefined
                    : () => {
                        void submitContinuation(
                          draft,
                          transport,
                          threadId,
                          tracker
                            ? () => tracker.beforeRun(threadId)
                            : undefined,
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
              {display.recovery?.state !== "resumed" && (
                <RecoveryNotice recovery={display.recovery} />
              )}
              {!!detail.data?.deferred_requests?.length && (
                <Decisions
                  threadId={threadId}
                  continuation={detail.data?.continuation_id}
                  reconcile={reconcile}
                />
              )}
              {!entries.length &&
                !draft.localInputs.length &&
                thread?.root_activity.state === "inactive" &&
                !showLive &&
                !history.isPending &&
                !history.error && (
                  <div
                    className={`${styles.empty} ${isCoordinator ? coordinatorStyles.intro : ""}`}
                  >
                    {isCoordinator ? (
                      <>
                        <h2>What are we working on?</h2>
                        <p>
                          Share a goal. I’ll help plan the work and bring
                          results back here.
                        </p>
                      </>
                    ) : (
                      <>
                        <h2>Start something together.</h2>
                        <p>
                          Write a prompt below. People on this conversation can
                          edit the same input.
                        </p>
                      </>
                    )}
                  </div>
                )}
            </div>
          </div>
        </div>
        {history.isPending &&
          !history.data &&
          !draft.localInputs.length &&
          !showLive && <p role="status">Loading saved history…</p>}
        {newOutput && (
          <Button
            className={styles.newOutput}
            variant="outline"
            onClick={backToLatest}
          >
            <ArrowDown />
            New output
          </Button>
        )}
        {detail.data && (
          <WorkInspector
            threadId={threadId}
            display={display}
            connected={connection === "Live"}
            reconcile={reconcile}
          />
        )}
        {!thread?.archived && (
          <ErrorNotice error={agentSelection.error || selectors.error} />
        )}
        {!thread?.archived && (agentSelection.isError || detail.isError) && (
          <Button
            variant="ghost"
            disabled={detail.isFetching}
            onClick={async () => {
              const refreshed = await detail.refetch();
              if (refreshed.isSuccess) agentSelection.reset();
            }}
          >
            Refresh agent selection before sending
          </Button>
        )}
        {!thread?.archived && (
          <Composer
            autoFocus={pageReady && search.get("compose") === "1"}
            threadId={threadId}
            activity={thread?.root_activity ?? { state: "inactive" }}
            canRun={
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
                ? "Updating conversation settings…"
                : !detail.data
                  ? "Loading conversation…"
                  : undefined
            }
            modelId={draft.modelId}
            leadingControls={
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
                  detail.isFetching ||
                  draft.submission.kind === "pending" ||
                  draft.submission.kind === "unknown"
                }
              />
            }
            controls={(expanded) => (
              <ThreadRunChoices
                expanded={expanded}
                catalog={selectors.data}
                agentId={thread?.configuration.agent_source.id ?? ""}
                defaultModelId={thread?.configuration.default_model_id}
                modelId={draft.modelId}
                controls={draft.controls}
                onControlsChange={(value) => {
                  draft.controls = value;
                  draft.notify();
                }}
                disabled={
                  !thread ||
                  agentSelection.isPending ||
                  detail.isFetching ||
                  draft.submission.kind === "pending" ||
                  draft.submission.kind === "unknown"
                }
                onAgentChange={(value) => agentSelection.mutate(value)}
                onModelChange={(value) => {
                  draft.modelId = value;
                  draft.controls = {};
                  draft.notify();
                }}
              />
            )}
            profile={profile}
            unauthorized={unauthorized}
            reconcile={reconcile}
          />
        )}
        {thread && !thread.archived && (
          <ComposerStatus
            threadId={threadId}
            receipt={receipt}
            busy={thread.root_activity.state !== "inactive"}
            liveTokens={showLive ? display.contextUsage?.tokens : undefined}
            savedGoal={thread.goal}
            onRetryGoal={
              thread.root_activity.state === "inactive" &&
              detail.data?.available_actions?.includes("run") &&
              draft.submission.kind !== "pending" &&
              draft.submission.kind !== "unknown"
                ? (objective) => {
                    const text = draft.doc.getText("text");
                    text.insert(
                      text.length,
                      `${text.length ? "\n\n" : ""}${objective}`,
                    );
                    draft.mode = "goal";
                    draft.notify();
                  }
                : undefined
            }
          />
        )}
        <ModalFrame
          open={dialog === "share"}
          onOpenChange={(open) => {
            if (!open) closeDialog();
          }}
          title="Share conversation"
          description="People need access to this instance to open the conversation. The link contains no API key."
          closeLabel="Close"
        >
          <TextField
            label="Conversation link"
            value={`${window.location.origin}/threads/${encodeURIComponent(threadId)}`}
            onChange={() => {}}
          />
          <Button
            onClick={() => {
              void navigator.clipboard
                .writeText(
                  `${window.location.origin}/threads/${encodeURIComponent(threadId)}`,
                )
                .then(
                  () => setMessage("Conversation link copied."),
                  () =>
                    setMessage(
                      "Could not copy the link. Select and copy it above.",
                    ),
                );
            }}
          >
            Copy link
          </Button>
          {message && <p role="status">{message}</p>}
        </ModalFrame>
        <ModalFrame
          open={rename}
          onOpenChange={setRename}
          title="Rename conversation"
          closeLabel="Close"
        >
          <form
            className={styles.form}
            onSubmit={(event) => {
              event.preventDefault();
              metadata.mutate({ title: title || null });
            }}
          >
            <TextField
              label="Title"
              value={title}
              onChange={(value) => setTitle(value.slice(0, 512))}
            />
            <ErrorNotice error={metadata.error} />
            <Button
              type="submit"
              loading={metadata.isPending}
              disabled={!thread}
            >
              Save title
            </Button>
          </form>
        </ModalFrame>
        <ModalFrame
          open={inspection}
          onOpenChange={setInspection}
          title="Conversation details"
          description="Inspect execution and configuration without changing the current Run."
          closeLabel="Close"
        >
          <ConversationDetails
            threadId={threadId}
            receipt={receipt}
            continuation={detail.data?.continuation_id}
            reconcile={reconcile}
          />
          {thread && (
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
                Archive conversation
              </Button>
            )}
        </ModalFrame>
      </div>
    </ConversationOpening>
  );
}

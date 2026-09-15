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
import { ArrowDown, Stop } from "@phosphor-icons/react";
import { result } from "../transport/client";
import { useTransport } from "../transport/context";
import { ErrorNotice, TextField } from "../shell/ui";
import type { Profile } from "../shell/presence";
import { readPreference, writePreference } from "../shell/preferences";
import { Composer, useDraft } from "./composer";
import { ComposerStatus } from "./composer-status";
import { Decisions } from "./decisions";
import { ConversationDetails } from "./details";
import { WorkInspector } from "./work-inspector";
import { Discussion } from "./comments";
import { refreshThreadLists, useHistory, useThread } from "./queries";
import { FocusDisplay, showFocusedOutput, watchThread } from "./stream";
import { LiveOutput, SavedEntry } from "./transcript";
import { savedToolGroups } from "./tool-presentation";
import styles from "./conversation.module.css";

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
  const history = useHistory(
    threadId,
    detail.data?.continuation_id,
    !!detail.data,
  );
  const [display] = useState(() => new FocusDisplay());
  const [connection, setConnection] = useState("Connecting");
  const [revision, setRevision] = useState(0);
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
  const olderAnchor = useRef<{ height: number; top: number } | null>(null);
  const [newOutput, setNewOutput] = useState(false);
  const reconcile = useCallback(() => {
    void queries.invalidateQueries({ queryKey: ["thread", threadId] });
    void refreshThreadLists(queries);
    void queries.invalidateQueries({ queryKey: ["child-saved-output"] });
  }, [queries, threadId]);
  useEffect(() => {
    let paint: ReturnType<typeof setTimeout> | undefined;
    let refresh: ReturnType<typeof setTimeout> | undefined;
    const close = watchThread(
      transport,
      threadId,
      display,
      () => {
        if (!paint)
          paint = setTimeout(() => {
            paint = undefined;
            setRevision((value) => value + 1);
          }, 50);
      },
      setConnection,
      () => {
        if (!refresh)
          refresh = setTimeout(() => {
            refresh = undefined;
            reconcile();
          }, 150);
      },
    );
    return () => {
      close();
      clearTimeout(paint);
      clearTimeout(refresh);
    };
  }, [transport, threadId, display, reconcile]);
  const entries = useMemo(
    () =>
      history.data?.pages
        .slice()
        .reverse()
        .flatMap((page) => page.entries) ?? [],
    [history.data],
  );
  const toolGroups = useMemo(() => savedToolGroups(entries), [entries]);
  const continuation = history.data?.pages[0]?.continuation_id;
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
  const showLive = showFocusedOutput(
    display,
    continuation,
    operation?.run_id,
    history.isPreviousHistory,
  );
  const visibleContent = `${continuation}:${entries.length}:${
    showLive
      ? [...display.blocks.values()]
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
    if (!element || !history.data) return;
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
    if (olderAnchor.current && !history.isFetchingNextPage) {
      element.scrollTop =
        olderAnchor.current.top +
        element.scrollHeight -
        olderAnchor.current.height;
      olderAnchor.current = null;
    } else if (follow.current) element.scrollTop = element.scrollHeight;
    else if (
      !restored &&
      !olderAnchor.current &&
      lastContent.current !== visibleContent
    )
      setNewOutput(true);
    lastContent.current = visibleContent;
  }, [revision, visibleContent, history.data, history.isFetchingNextPage]);
  useLayoutEffect(() => {
    const element = reader.current;
    return () => {
      if (element)
        writePreference(
          `scroll.${threadId}`,
          JSON.stringify({ top: element.scrollTop, follow: follow.current }),
        );
    };
  }, [threadId, !!detail.data]);
  useEffect(() => {
    if (detail.data && !detail.data.thread.archived)
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
  const stop = useMutation({
    mutationFn: (receipt: string) =>
      result(
        transport.client.POST("/api/operations/{receipt_id}/cancel", {
          params: { path: { receipt_id: receipt } },
        }),
      ),
    onSuccess: (outcome) => {
      setMessage(
        outcome.accepted
          ? "Stop accepted for this operation."
          : "This operation is no longer cancellable.",
      );
      reconcile();
    },
    onError: reconcile,
  });
  if (!detail.data)
    return (
      <div>
        <ErrorNotice error={detail.error} retry={() => void detail.refetch()} />
        {!detail.error && <p role="status">Loading conversation…</p>}
        <Link to="/">Return to workbench</Link>
      </div>
    );
  const thread = detail.data.thread;
  if (thread.parent_thread_id)
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
    <Discussion
      threadId={threadId}
      profile={profile}
      listOpen={dialog === "comments"}
      closeList={closeDialog}
    >
      <div className={styles.page}>
        {(connection !== "Live" ||
          thread.root_activity.state !== "inactive") && (
          <div className={styles.activityBar}>
            <small role="status">
              {connection !== "Live" ? connection : thread.root_activity.state}
            </small>
            {thread.root_activity.available_actions?.includes("cancel") &&
              thread.root_activity.receipt_id && (
                <Button
                  variant="outline"
                  loading={stop.isPending}
                  onClick={() => stop.mutate(thread.root_activity.receipt_id!)}
                >
                  <Stop />
                  Stop
                </Button>
              )}
          </div>
        )}

        {thread.archived && (
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
          error={detail.error || history.error || metadata.error || stop.error}
          retry={reconcile}
        />
        <div
          ref={reader}
          className={styles.reading}
          onScroll={() => {
            const element = reader.current!;
            follow.current =
              element.scrollHeight - element.scrollTop - element.clientHeight <
              64;
            if (follow.current) setNewOutput(false);
          }}
        >
          <div className={styles.transcript}>
            {history.hasNextPage && (
              <Button
                variant="ghost"
                loading={history.isFetchingNextPage}
                onClick={() => {
                  if (reader.current)
                    olderAnchor.current = {
                      height: reader.current.scrollHeight,
                      top: reader.current.scrollTop,
                    };
                  void history.fetchNextPage();
                }}
              >
                Load earlier messages
              </Button>
            )}
            {entries.map((entry, index) => (
              <div
                key={`${continuation}:${entry.position}`}
                data-presence-anchor={`entry:${continuation}:${entry.position}`}
              >
                <SavedEntry
                  entry={entry}
                  continuation={
                    index > 0 &&
                    !entries[index - 1].parts.some(
                      (part) => part.kind === "user" || part.kind === "media",
                    ) &&
                    !entry.parts.some(
                      (part) => part.kind === "user" || part.kind === "media",
                    )
                  }
                  toolGroups={toolGroups}
                  threadId={threadId}
                />
              </div>
            ))}
            {showLive && (
              <LiveOutput
                blocks={[...display.blocks.values()]}
                gap={display.gap}
                threadId={threadId}
              />
            )}
            {!!detail.data.deferred_requests?.length && (
              <Decisions
                threadId={threadId}
                continuation={detail.data.continuation_id}
                reconcile={reconcile}
              />
            )}
            {!entries.length &&
              !showLive &&
              !history.isPending &&
              !history.error && (
                <div className={styles.empty}>
                  <h2>Start something together.</h2>
                  <p>
                    Write a prompt below. People on this conversation can edit
                    the same input.
                  </p>
                </div>
              )}
          </div>
        </div>
        {history.isPending && !history.data && (
          <p role="status">Loading saved history…</p>
        )}
        {newOutput && (
          <Button
            className={styles.newOutput}
            variant="outline"
            onClick={() => {
              follow.current = true;
              setNewOutput(false);
              reader.current?.scrollTo({ top: reader.current.scrollHeight });
            }}
          >
            <ArrowDown />
            New output
          </Button>
        )}
        <WorkInspector
          threadId={threadId}
          continuation={detail.data.continuation_id}
          display={display}
          live={showLive}
          reconcile={reconcile}
        />
        {!thread.archived && (
          <Composer
            autoFocus={search.get("compose") === "1"}
            threadId={threadId}
            activity={thread.root_activity}
            canRun={detail.data.available_actions?.includes("run") ?? false}
            profile={profile}
            unauthorized={unauthorized}
            reconcile={reconcile}
          />
        )}
        {!thread.archived && <ComposerStatus threadId={threadId} />}
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
            <Button type="submit" loading={metadata.isPending}>
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
            continuation={detail.data.continuation_id}
            reconcile={reconcile}
          />
          {!thread.archived &&
            detail.data.available_actions?.includes("archive") && (
              <Button
                variant="outline"
                onClick={() => {
                  if (
                    window.confirm(
                      "Archive this conversation? Its saved history will be retained.",
                    )
                  )
                    metadata.mutate({ archived: true });
                }}
              >
                Archive conversation
              </Button>
            )}
        </ModalFrame>
      </div>
    </Discussion>
  );
}

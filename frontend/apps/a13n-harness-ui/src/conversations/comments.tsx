import {
  createContext,
  useCallback,
  useEffect,
  useContext,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  useInfiniteQuery,
  useQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import {
  Button,
  Sheet,
  SheetPopup,
  SheetHeader,
  SheetTitle,
  SheetDescription,
  SheetPanel,
  ModalFrame,
  Popover,
  PopoverPopup,
  PopoverTitle,
  Menu,
  MenuTrigger,
  MenuPopup,
  MenuItem,
} from "a13n-ui";
import {
  ArrowClockwise,
  ChatCircle,
  DotsThree,
  X,
} from "@phosphor-icons/react";
import { ApiError, result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import type { Profile } from "../shell/presence";
import { ErrorNotice } from "../shell/ui";
import { ConfirmAction } from "../shell/confirm-action";
import { useDraft } from "./composer";
import { MessageText } from "./message-text";
import { selectedSource } from "./comment-selection";
import styles from "./conversation.module.css";
import { attachmentSelections } from "./inline-attachments";

type Target = Schema<"SavedOutputTarget">;
type Selection = Schema<"CommentSelection">;
export type CommentDraft = {
  publication: Schema<"CommentPublication">;
  text: string;
  status: "editing" | "pending" | "unknown" | "published";
  error?: unknown;
  expectedVersion?: number;
};
export const CommentDrafts = createContext(new Map<string, CommentDraft>());
const targetKey = (target: Target) =>
  JSON.stringify([
    target.producing_thread_id,
    target.source_id,
    target.location.kind,
    target.location.kind === "root_text"
      ? [target.location.message, target.location.part]
      : [target.location.execution_id, target.location.activity ?? null],
  ]);
const DiscussionContext = createContext<{
  begin: (
    target: Target,
    text: string,
    selection?: Selection,
    anchor?: HTMLElement,
  ) => void;
  browse: (target?: Target, anchor?: HTMLElement, ids?: string[]) => void;
  register: (target: Target, text: string, element: HTMLElement) => () => void;
  inspect: (target: Target) => void;
  comments: Schema<"OutputComment">[];
  open: boolean;
} | null>(null);

export function CommentListButton() {
  const discussion = useContext(DiscussionContext);
  return (
    <Button variant="ghost" onClick={() => discussion?.browse()}>
      <ChatCircle />
      Comments
    </Button>
  );
}
export function SavedOutput({
  target,
  text,
  truncated = false,
}: {
  target?: Target | null;
  text: string;
  truncated?: boolean;
}) {
  const discussion = useContext(DiscussionContext);
  const container = useRef<HTMLDivElement>(null);
  const [selection, setSelection] = useState<Selection>();
  const [ambiguous, setAmbiguous] = useState(false);
  const [originalShown, setOriginalShown] = useState(false);
  const [selectionTop, setSelectionTop] = useState(0);
  const [selectionLeft, setSelectionLeft] = useState(0);
  const register = discussion?.register;
  useEffect(() => {
    if (register && target && container.current)
      return register(target, text, container.current);
  }, [register, target, text]);
  useEffect(() => {
    if (!discussion?.open) setSelection(undefined);
    const outside = (event: PointerEvent) => {
      if (
        !discussion?.open &&
        event.target instanceof Node &&
        !container.current?.contains(event.target)
      )
        setSelection(undefined);
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [discussion?.open]);
  const read = (event: { target: EventTarget }) => {
    if (event.target instanceof Element && event.target.closest("button"))
      return;
    if (truncated) return;
    const current = window.getSelection();
    if (
      !container.current ||
      !current ||
      !container.current.contains(current.anchorNode)
    )
      return;
    const next = selectedSource(container.current, text, current);
    // Pointer activation of the selection action must not erase its frozen quote.
    if (current.isCollapsed) {
      setSelection(undefined);
      setAmbiguous(false);
      return;
    }
    setSelection(next);
    setAmbiguous(!next);
    const range = current.rangeCount ? current.getRangeAt(0) : undefined;
    if (range && typeof range.getBoundingClientRect === "function") {
      setSelectionLeft(
        Math.max(
          0,
          Math.min(
            container.current.clientWidth - 240,
            range.getBoundingClientRect().left -
              container.current.getBoundingClientRect().left,
          ),
        ),
      );
      setSelectionTop(
        Math.max(
          0,
          range.getBoundingClientRect().bottom -
            container.current.getBoundingClientRect().top +
            6,
        ),
      );
    }
  };
  const matches = target
    ? (discussion?.comments.filter(
        (comment) => targetKey(comment.target) === targetKey(target),
      ) ?? [])
    : [];
  const highlight = (element: HTMLElement, keyboard = false) => {
    if (!keyboard && window.getSelection()?.isCollapsed === false) return false;
    const mark = element.closest<HTMLElement>("[data-comment-ids]");
    if (!mark || !target) return false;
    discussion?.browse(target, mark, mark.dataset.commentIds?.split(" "));
    return true;
  };
  return (
    <div
      ref={container}
      tabIndex={-1}
      className={styles.savedOutput}
      onMouseUp={read}
      onKeyUp={read}
      onTouchEnd={read}
      onClick={(event) => {
        if (highlight(event.target as HTMLElement)) event.preventDefault();
      }}
      onKeyDown={(event) => {
        if (
          (event.key === "Enter" || event.key === " ") &&
          (event.target as HTMLElement).matches("[data-comment-ids]")
        ) {
          event.preventDefault();
          highlight(event.target as HTMLElement, true);
        }
      }}
    >
      <MessageText
        text={text}
        selectable={!!target && !truncated}
        highlights={
          truncated
            ? []
            : matches.flatMap((comment) =>
                comment.selection
                  ? [{ id: comment.comment_id, selection: comment.selection }]
                  : [],
              )
        }
      />
      {(originalShown || !discussion) && (
        <details className={styles.rawSource} open>
          <summary>{truncated ? "Displayed excerpt" : "Original text"}</summary>
          <pre
            className={styles.code}
            data-source-start={truncated ? undefined : "0"}
            data-source-end={text.length}
          >
            {text}
          </pre>
        </details>
      )}
      {discussion && target && (
        <>
          {selection && (
            <div
              className={styles.selectionAction}
              style={{ top: selectionTop, left: selectionLeft }}
            >
              <Button
                variant="outline"
                size="sm"
                onMouseDown={(event) => event.preventDefault()}
                onClick={(event) =>
                  discussion.begin(target, text, selection, event.currentTarget)
                }
              >
                <ChatCircle />
                Comment on selection
              </Button>
              <Button
                variant="ghost"
                size="icon"
                aria-label="Dismiss selection"
                onClick={() => {
                  setSelection(undefined);
                  window.getSelection()?.removeAllRanges();
                }}
              >
                <X />
              </Button>
            </div>
          )}
          <div className={styles.outputTools}>
            <Button
              variant="ghost"
              size="sm"
              aria-label="Add comment"
              title="Comment on this output"
              onClick={(event) =>
                discussion.begin(target, text, undefined, event.currentTarget)
              }
            >
              <ChatCircle />
              Add comment
            </Button>
            {!!matches.length && (
              <Button
                variant="ghost"
                size="sm"
                onClick={(event) =>
                  discussion.browse(target, event.currentTarget)
                }
              >
                View comments
              </Button>
            )}
            {truncated && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => discussion.inspect(target)}
              >
                View complete original
              </Button>
            )}
            <Menu>
              <MenuTrigger
                render={<Button variant="ghost" size="icon" />}
                aria-label="Output actions"
              >
                <DotsThree />
              </MenuTrigger>
              <MenuPopup align="start">
                <MenuItem onClick={() => setOriginalShown(!originalShown)}>
                  {originalShown ? "Hide original text" : "Show original text"}
                </MenuItem>
              </MenuPopup>
            </Menu>
          </div>
          {ambiguous && (
            <div className={styles.selectionHint}>
              <span>This selection cannot be quoted precisely.</span>
              <Button
                variant="ghost"
                size="sm"
                onClick={(event) => {
                  setAmbiguous(false);
                  discussion.begin(
                    target,
                    text,
                    undefined,
                    event.currentTarget,
                  );
                }}
              >
                Comment on whole response
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setAmbiguous(false);
                  setOriginalShown(true);
                }}
              >
                Open original text
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function useOutputComments(threadId: string, filter?: Target) {
  const transport = useTransport();
  return useInfiniteQuery({
    queryKey: ["comments", threadId, filter ? targetKey(filter) : "all"],
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) =>
      result(
        transport.client.GET("/api/threads/{thread_id}/comments", {
          params: {
            path: { thread_id: threadId },
            query: {
              cursor: pageParam,
              target: filter ? JSON.stringify(filter) : undefined,
              newest_first: true,
              limit: 20,
            },
          },
          signal,
        }),
      ),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
}

export function Discussion({
  threadId,
  profile,
  children,
  listOpen = false,
  closeList,
  onReferenceAdded,
}: {
  threadId: string;
  profile: Profile;
  children: ReactNode;
  listOpen?: boolean;
  closeList?: () => void;
  onReferenceAdded?: () => void;
}) {
  const transport = useTransport();
  const queries = useQueryClient();
  const drafts = useContext(CommentDrafts);
  const composer = useDraft(threadId);
  const [, refresh] = useState(0);
  const update = () => refresh((value) => value + 1);
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState<Target>();
  const [focusedIds, setFocusedIds] = useState<string[]>();
  const [original, setOriginal] = useState<Target>();
  const [message, setMessage] = useState("");
  const [deleting, setDeleting] = useState<Schema<"OutputComment">>();
  const [anchor, setAnchor] = useState<HTMLElement>();
  const transferFocus = useRef(false);
  const outputs = useRef(
    new Map<string, { text: string; element: HTMLElement }>(),
  );
  const register = useCallback(
    (target: Target, text: string, element: HTMLElement) => {
      const key = targetKey(target);
      const entry = { text, element };
      outputs.current.set(key, entry);
      return () => {
        if (outputs.current.get(key) === entry) outputs.current.delete(key);
      };
    },
    [],
  );
  const draft = drafts.get(threadId);
  const allComments = useOutputComments(threadId);
  const comments = useOutputComments(threadId, filter);
  useEffect(() => {
    if (listOpen) {
      setFilter(undefined);
      setFocusedIds(undefined);
      setAnchor(undefined);
      setMessage("");
      transferFocus.current = false;
    }
  }, [listOpen]);
  const rows = comments.data?.pages.flatMap((page) => page.comments) ?? [];
  const visible = focusedIds
    ? rows.filter((row) => focusedIds.includes(row.comment_id))
    : rows;
  const groups = new Map<
    string,
    { target: Target; comments: Schema<"OutputComment">[] }
  >();
  for (const comment of visible) {
    const key = targetKey(comment.target);
    if (!groups.has(key))
      groups.set(key, { target: comment.target, comments: [] });
    groups.get(key)!.comments.push(comment);
  }
  const close = () => {
    setOpen(false);
    closeList?.();
  };
  const refreshComments = () =>
    queries.invalidateQueries({ queryKey: ["comments", threadId] });
  const publish = async (current: CommentDraft) => {
    if (current.status === "pending" || current.status === "published") return;
    const uncertain = current.status === "unknown";
    current.status = "pending";
    current.error = undefined;
    update();
    try {
      const saved =
        current.expectedVersion === undefined
          ? await result(
              transport.client.POST("/api/threads/{thread_id}/comments", {
                params: { path: { thread_id: threadId } },
                body: current.publication,
              }),
            )
          : await result(
              transport.client.PATCH(
                "/api/threads/{thread_id}/comments/{comment_id}",
                {
                  params: {
                    path: {
                      thread_id: threadId,
                      comment_id: current.publication.comment_id,
                    },
                  },
                  body: {
                    body: current.publication.body,
                    expected_version: current.expectedVersion,
                  },
                },
              ),
            );
      if (saved.comment_id !== current.publication.comment_id)
        throw new Error("The acknowledgement did not match this comment.");
      current.status = "published";
      if (drafts.get(threadId) === current) drafts.delete(threadId);
      setFocusedIds(undefined);
      setMessage(
        current.expectedVersion === undefined
          ? "Comment posted. Not sent to the agent."
          : "Comment updated. Existing message references are unchanged.",
      );
      void refreshComments();
    } catch (error) {
      current.status =
        error instanceof ApiError &&
        error.status >= 400 &&
        error.status < 500 &&
        (!uncertain ||
          error.status === 409 ||
          (current.expectedVersion !== undefined &&
            error.code === "comment_missing"))
          ? "editing"
          : "unknown";
      current.error = error;
    } finally {
      update();
    }
  };
  const capture = useMutation({
    mutationFn: async (comment: Schema<"OutputComment">) => {
      if (attachmentSelections(composer.doc).length >= 8)
        throw new Error(
          "Remove an attachment before adding a comment (limit: eight).",
        );
      const incarnation = composer.draftId;
      const attachment = await result(
        transport.client.POST(
          "/api/threads/{thread_id}/comments/{comment_id}/capture",
          {
            params: {
              path: { thread_id: threadId, comment_id: comment.comment_id },
              query: { expected_version: comment.version ?? 1 },
            },
          },
        ),
      );
      if (incarnation !== composer.draftId || composer.replacement)
        throw new Error(
          "The shared draft changed. Rejoin before adding this comment.",
        );
      if (attachmentSelections(composer.doc).length >= 8)
        throw new Error(
          "The message now has eight attachments. Remove one and try again.",
        );
      queries.setQueryData(
        ["thread", threadId, "attachment", attachment.attachment_id],
        attachment,
      );
      composer.addAttachment(attachment.attachment_id);
      transferFocus.current = true;
      close();
      onReferenceAdded?.();
    },
    onError: () => {
      void refreshComments();
    },
  });
  const remove = useMutation({
    mutationFn: async (comment: Schema<"OutputComment">) => {
      await transport.client.DELETE(
        "/api/threads/{thread_id}/comments/{comment_id}",
        {
          params: {
            path: { thread_id: threadId, comment_id: comment.comment_id },
            query: { expected_version: comment.version ?? 1 },
          },
        },
      );
      if (drafts.get(threadId)?.publication.comment_id === comment.comment_id) {
        drafts.delete(threadId);
        update();
      }
      setFocusedIds(undefined);
      setMessage("Comment deleted. Existing message references are unchanged.");
      // The selected highlight may disappear on refetch; keep the discussion on its response.
      if (anchor?.matches("[data-comment-ids]"))
        setAnchor(outputs.current.get(targetKey(comment.target))?.element);
      await refreshComments();
    },
    onError: () => {
      void refreshComments();
    },
  });
  const begin = (
    target: Target,
    text: string,
    selection?: Selection,
    element?: HTMLElement,
  ) => {
    const retained =
      draft &&
      draft.status !== "published" &&
      (draft.publication.body || draft.status !== "editing");
    if (retained) {
      setMessage(
        "Your unfinished comment is kept here. Save or discard it before starting another.",
      );
    } else {
      drafts.set(threadId, {
        text,
        status: "editing",
        publication: {
          comment_id: `comment-${crypto.randomUUID().replaceAll("-", "")}`,
          target,
          selection: selection ?? null,
          author: { display_name: profile.display_name },
          body: "",
        },
      });
      setMessage("");
      update();
    }
    transferFocus.current = false;
    setOriginal(undefined);
    setFocusedIds(undefined);
    setFilter(retained ? draft.publication.target : target);
    setAnchor(
      retained && targetKey(draft.publication.target) !== targetKey(target)
        ? undefined
        : element,
    );
    setOpen(true);
  };
  const edit = (comment: Schema<"OutputComment">) => {
    if (
      draft &&
      draft.status !== "published" &&
      (draft.publication.body || draft.status !== "editing")
    ) {
      setMessage(
        "Save or discard your unfinished comment before editing another.",
      );
      return;
    }
    drafts.set(threadId, {
      publication: {
        comment_id: comment.comment_id,
        target: comment.target,
        selection: comment.selection,
        author: comment.author,
        body: comment.body,
      },
      text: outputs.current.get(targetKey(comment.target))?.text ?? "",
      expectedVersion: comment.version ?? 1,
      status: "editing",
    });
    setFilter(comment.target);
    setFocusedIds(undefined);
    setMessage("");
    update();
  };
  const inspect = (target: Target) => {
    const output = outputs.current.get(targetKey(target));
    if (output?.element.isConnected) {
      transferFocus.current = true;
      close();
      let parent = output.element.parentElement;
      while (parent) {
        if (parent instanceof HTMLDetailsElement) parent.open = true;
        parent = parent.parentElement;
      }
      output.element.scrollIntoView({ block: "center", behavior: "smooth" });
      output.element.focus({ preventScroll: true });
    } else setOriginal(target);
  };
  const newComment = (target: Target) => {
    const output = outputs.current.get(targetKey(target));
    begin(target, output?.text ?? "", undefined, anchor);
  };
  const editor =
    draft && draft.status !== "published" ? (
      <section className={styles.commentDraft}>
        <h3>
          {draft.expectedVersion === undefined ? "New comment" : "Edit comment"}
        </h3>
        {draft.publication.selection ? (
          <blockquote>{draft.publication.selection.quote}</blockquote>
        ) : (
          <CommentTarget
            threadId={threadId}
            target={draft.publication.target}
            text={draft.text || undefined}
          />
        )}
        <label className={styles.form}>
          Comment
          <textarea
            key={draft.publication.comment_id}
            autoFocus
            rows={3}
            placeholder="What would you like to point out?"
            value={draft.publication.body}
            disabled={draft.status !== "editing"}
            onChange={(event) => {
              draft.publication = {
                ...draft.publication,
                body: event.target.value,
              };
              update();
            }}
          />
        </label>
        <small>
          Posting as {draft.publication.author.display_name}. Not sent to the
          agent.
        </small>
        <ErrorNotice error={draft.error} />
        {draft.expectedVersion !== undefined &&
          draft.error instanceof ApiError &&
          draft.error.code === "comment_version_conflict" && (
            <EditConflict
              threadId={threadId}
              draft={draft}
              onContinue={update}
            />
          )}
        <div className={styles.commentActions}>
          {draft.status === "editing" && (
            <Button
              disabled={
                !draft.publication.body.trim() ||
                [...draft.publication.body].length > 16384 ||
                !draft.publication.author.display_name.trim() ||
                (draft.error instanceof ApiError &&
                  draft.error.code === "comment_version_conflict")
              }
              onClick={() => void publish(draft)}
            >
              {draft.expectedVersion === undefined
                ? "Post comment"
                : "Save changes"}
            </Button>
          )}
          {draft.status === "pending" && <p role="status">Saving…</p>}
          {draft.status === "unknown" && (
            <>
              <p>
                We could not confirm the save. Your text is kept; retry checks
                the same change.
              </p>
              <Button onClick={() => void publish(draft)}>
                Check comment status
              </Button>
            </>
          )}
          {draft.status === "editing" && (
            <ConfirmAction
              key={draft.publication.comment_id}
              trigger={<Button variant="ghost">Discard draft</Button>}
              title="Discard this private comment draft?"
              description="Your unpublished changes will be lost."
              confirmLabel="Discard draft"
              destructive
              confirmationRequired={!!draft.publication.body}
              onConfirm={() => {
                drafts.delete(threadId);
                update();
              }}
            />
          )}
        </div>
      </section>
    ) : null;
  const panel = (
    <div className={`${styles.form} ${styles.commentsPanel}`}>
      {message && (
        <p role="status" className={styles.commentNotice}>
          {message}
        </p>
      )}
      <ErrorNotice error={comments.error || capture.error || remove.error} />
      <div className={styles.commentToolbar}>
        <span>{filter ? "This response" : "All responses"}</span>
        {focusedIds && (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setFocusedIds(undefined)}
          >
            All comments
          </Button>
        )}
        {filter && !anchor && (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              setFilter(undefined);
              setFocusedIds(undefined);
            }}
          >
            All responses
          </Button>
        )}
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Refresh comments"
          title="Refresh comments"
          loading={comments.isFetching && !comments.isFetchingNextPage}
          onClick={() => void refreshComments()}
        >
          <ArrowClockwise />
        </Button>
      </div>
      {comments.isPending && <p>Loading comments…</p>}
      {!draft && comments.isSuccess && !visible.length && (
        <div className={styles.commentEmpty}>
          <ChatCircle size={28} aria-hidden="true" />
          <strong>No comments yet</strong>
          <p>
            {filter
              ? "Leave a comment on this response."
              : "Select text in a saved response, or use Add comment below it."}
          </p>
        </div>
      )}
      {draft &&
        (draft.expectedVersion === undefined ||
          !visible.some(
            (row) => row.comment_id === draft.publication.comment_id,
          )) &&
        editor}
      {[...groups].map(([key, group]) => (
        <section key={key} className={styles.commentGroup}>
          <CommentTarget
            threadId={threadId}
            target={group.target}
            text={outputs.current.get(key)?.text}
            inspect={inspect}
          />
          {group.comments.map((comment) => (
            <article key={comment.comment_id} className={styles.commentCard}>
              {draft?.publication.comment_id === comment.comment_id ? (
                editor
              ) : (
                <>
                  <header>
                    <strong>{comment.author.display_name}</strong>
                    <time
                      dateTime={comment.created_at}
                      title={new Date(comment.created_at).toLocaleString()}
                    >
                      {new Date(comment.created_at).toLocaleDateString(
                        undefined,
                        { month: "short", day: "numeric" },
                      )}
                      {comment.updated_at ? " · edited" : ""}
                    </time>
                    <Menu>
                      <MenuTrigger
                        disabled={remove.isPending || capture.isPending}
                        render={<Button variant="ghost" size="icon-sm" />}
                        aria-label="Comment actions"
                      >
                        <DotsThree />
                      </MenuTrigger>
                      <MenuPopup align="end">
                        <MenuItem onClick={() => edit(comment)}>
                          Edit comment
                        </MenuItem>
                        <MenuItem onClick={() => setDeleting(comment)}>
                          Delete comment…
                        </MenuItem>
                      </MenuPopup>
                    </Menu>
                  </header>
                  {comment.selection ? (
                    <blockquote>{comment.selection.quote}</blockquote>
                  ) : (
                    <small>Whole response</small>
                  )}
                  <MessageText text={comment.body} />
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={
                      capture.isPending ||
                      !!composer.replacement ||
                      remove.isPending
                    }
                    onClick={() => capture.mutate(comment)}
                  >
                    Add to message
                  </Button>
                </>
              )}
            </article>
          ))}
        </section>
      ))}
      {comments.hasNextPage && (
        <Button
          variant="ghost"
          loading={comments.isFetchingNextPage}
          onClick={() => void comments.fetchNextPage()}
        >
          Load more comments
        </Button>
      )}
      {!draft && filter && (
        <Button variant="outline" onClick={() => newComment(filter)}>
          <ChatCircle />
          Add comment
        </Button>
      )}
    </div>
  );
  return (
    <DiscussionContext
      value={{
        begin,
        register,
        browse: (target, element, ids) => {
          transferFocus.current = false;
          setFilter(target);
          setFocusedIds(ids);
          setAnchor(element);
          setOpen(true);
          setMessage("");
        },
        comments:
          allComments.data?.pages.flatMap((page) => page.comments) ?? [],
        inspect: setOriginal,
        open: open || listOpen,
      }}
    >
      {children}
      <Popover
        open={open && !!anchor && !listOpen}
        onOpenChange={(next) => {
          // Sibling confirmation/source dialogs temporarily take focus, not the discussion.
          if (next || (!deleting && !original && !remove.isPending))
            setOpen(next);
        }}
      >
        <PopoverPopup
          anchor={anchor}
          side="right"
          align="start"
          sideOffset={12}
          className={styles.inlineDiscussion}
          finalFocus={() =>
            transferFocus.current ? false : anchor?.isConnected ? anchor : false
          }
        >
          <div className={styles.inlineHeading}>
            <PopoverTitle>Comments</PopoverTitle>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Close comments"
              onClick={close}
            >
              <X />
            </Button>
          </div>
          {panel}
        </PopoverPopup>
      </Popover>
      <Sheet
        open={(open && !anchor) || listOpen}
        onOpenChange={(next) => {
          setOpen(next);
          if (!next) closeList?.();
        }}
      >
        <SheetPopup
          finalFocus={() => (transferFocus.current ? false : undefined)}
          closeProps={{ "aria-label": "Close comments" }}
        >
          <SheetHeader>
            <SheetTitle>Comments</SheetTitle>
            <SheetDescription>
              Discuss responses here. Add a comment to your message when you
              want the agent to act on it.
            </SheetDescription>
          </SheetHeader>
          <SheetPanel>{panel}</SheetPanel>
        </SheetPopup>
      </Sheet>
      <ModalFrame
        open={!!deleting}
        onOpenChange={(next) => {
          if (!next) setDeleting(undefined);
        }}
        title="Delete this comment?"
        description="The comment and its highlight will be removed. Copies already added to messages will stay unchanged."
        closeLabel="Close confirmation"
        footer={
          <>
            <Button variant="outline" onClick={() => setDeleting(undefined)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => {
                if (deleting) remove.mutate(deleting);
                setDeleting(undefined);
              }}
            >
              Delete comment
            </Button>
          </>
        }
      >
        {deleting && <p>{deleting.body}</p>}
      </ModalFrame>
      <ModalFrame
        open={!!original}
        onOpenChange={(value) => {
          if (!value) setOriginal(undefined);
        }}
        title="Original saved output"
        description="The original response this comment refers to."
        closeLabel="Close"
      >
        {original && <OriginalOutput threadId={threadId} target={original} />}
      </ModalFrame>
    </DiscussionContext>
  );
}

function EditConflict({
  threadId,
  draft,
  onContinue,
}: {
  threadId: string;
  draft: CommentDraft;
  onContinue: () => void;
}) {
  const { client } = useTransport();
  const latest = useQuery({
    queryKey: ["comments", threadId, "conflict", draft.publication.comment_id],
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/threads/{thread_id}/comments/{comment_id}", {
          params: {
            path: {
              thread_id: threadId,
              comment_id: draft.publication.comment_id,
            },
          },
          signal,
        }),
      ),
    retry: false,
  });
  return (
    <div className={`${styles.form} ${styles.commentConflict}`}>
      <strong>Latest saved comment</strong>
      <ErrorNotice error={latest.error} />
      {latest.isPending && <p>Loading latest version…</p>}
      {latest.data && (
        <>
          <MessageText text={latest.data.body} />
          <small>
            Your edit is kept above. Review this version before saving again.
          </small>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              draft.expectedVersion = latest.data.version ?? 1;
              draft.error = undefined;
              onContinue();
            }}
          >
            Continue with my edit
          </Button>
        </>
      )}
    </div>
  );
}

function CommentTarget({
  threadId,
  target,
  text,
  inspect,
}: {
  threadId: string;
  target: Target;
  text?: string;
  inspect?: (target: Target) => void;
}) {
  const { client } = useTransport();
  const preview = useQuery({
    queryKey: ["comment-preview", threadId, targetKey(target)],
    enabled: text === undefined,
    queryFn: ({ signal }) =>
      result(
        client.POST("/api/threads/{thread_id}/saved-output", {
          params: { path: { thread_id: threadId }, query: { limit: 240 } },
          body: target,
          signal,
        }),
      ),
    retry: false,
  });
  return (
    <div className={styles.commentTarget}>
      <small>
        {target.location.kind === "child_text" ? "Child response" : "Response"}
      </small>
      <p>
        {text?.slice(0, 240) ??
          preview.data?.text ??
          (preview.isError
            ? "Original response unavailable"
            : "Loading response…")}
      </p>
      {inspect && (
        <Button variant="ghost" size="sm" onClick={() => inspect(target)}>
          View response
        </Button>
      )}
    </div>
  );
}
function OriginalOutput({
  threadId,
  target,
}: {
  threadId: string;
  target: Target;
}) {
  const { client } = useTransport();
  const output = useInfiniteQuery({
    queryKey: ["comment-output", threadId, targetKey(target)],
    initialPageParam: 0,
    queryFn: ({ pageParam, signal }) =>
      result(
        client.POST("/api/threads/{thread_id}/saved-output", {
          params: {
            path: { thread_id: threadId },
            query: { offset: pageParam },
          },
          body: target,
          signal,
        }),
      ),
    getNextPageParam: (page) => page.next_offset ?? undefined,
  });
  return (
    <div className={styles.form}>
      <ErrorNotice error={output.error} />
      {output.data?.pages.map((page) => (
        <OriginalWindow key={page.offset} page={page} />
      ))}
      {output.isPending && <p>Loading original output…</p>}
      {output.hasNextPage && (
        <Button
          loading={output.isFetchingNextPage}
          variant="outline"
          onClick={() => void output.fetchNextPage()}
        >
          Load more original text
        </Button>
      )}
    </div>
  );
}
export function OriginalWindow({ page }: { page: Schema<"SavedOutputView"> }) {
  const discussion = useContext(DiscussionContext);
  const container = useRef<HTMLDivElement>(null);
  const [selection, setSelection] = useState<Selection>();
  const read = () => {
    if (!container.current) return;
    const local = selectedSource(
      container.current,
      page.text,
      window.getSelection(),
    );
    setSelection(
      local
        ? {
            ...local,
            start: page.offset + local.start,
            end: page.offset + local.end,
          }
        : undefined,
    );
  };
  return (
    <div ref={container} onMouseUp={read} onKeyUp={read} onTouchEnd={read}>
      <small>
        Source characters {page.offset + 1}–
        {page.offset + [...page.text].length} of {page.total_characters}
      </small>
      <pre className={styles.code} data-source-start="0">
        {page.text}
      </pre>
      {selection && discussion && (
        <Button
          variant="outline"
          size="sm"
          onClick={() => discussion.begin(page.target, page.text, selection)}
        >
          Comment on original selection
        </Button>
      )}
    </div>
  );
}
export function ChildSavedOutputs({
  threadId,
  executionId,
}: {
  threadId: string;
  executionId: string;
}) {
  const { client } = useTransport();
  const queries = useQueryClient();
  const [open, setOpen] = useState(false);
  const output = useInfiniteQuery({
    queryKey: ["child-saved-output", threadId, executionId],
    enabled: open,
    retry: (count, error) =>
      !(error instanceof ApiError && error.status < 500) && count < 2,
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam, signal }) =>
      result(
        client.GET(
          "/api/threads/{thread_id}/children/{execution_id}/saved-output",
          {
            params: {
              path: { thread_id: threadId, execution_id: executionId },
              query: { cursor: pageParam },
            },
            signal,
          },
        ),
      ),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  return (
    <details
      onToggle={(event) => setOpen(event.currentTarget.open)}
      className={styles.activity}
    >
      <summary>Saved child output and comments</summary>
      <ErrorNotice
        error={
          output.error instanceof ApiError &&
          output.error.code === "comment_source_unavailable"
            ? undefined
            : output.error
        }
        retry={() =>
          void queries.resetQueries({
            queryKey: ["child-saved-output", threadId, executionId],
            exact: true,
          })
        }
      />
      {output.isFetching && <p>Loading saved child output…</p>}
      {((output.error instanceof ApiError &&
        output.error.code === "comment_source_unavailable") ||
        (output.data &&
          !output.data.pages.some((page) => page.outputs.length))) && (
        <p>No retained saved text is available yet.</p>
      )}
      {output.data?.pages
        .flatMap((page) => page.outputs)
        .map((item) =>
          item.target.location.kind === "child_text" &&
          item.target.location.activity != null ? (
            <details key={targetKey(item.target)} className={styles.activity}>
              <summary>
                Recorded text · activity {item.target.location.activity + 1}
              </summary>
              <SavedOutput target={item.target} text={item.text} />
            </details>
          ) : (
            <SavedOutput
              key={targetKey(item.target)}
              target={item.target}
              text={item.text}
            />
          ),
        )}
      {output.hasNextPage && (
        <Button
          loading={output.isFetchingNextPage}
          variant="outline"
          onClick={() => void output.fetchNextPage()}
        >
          Load more child output
        </Button>
      )}
    </details>
  );
}

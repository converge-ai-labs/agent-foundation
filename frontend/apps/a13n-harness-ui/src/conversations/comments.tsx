import {
  createContext,
  useContext,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { ChatCircle } from "@phosphor-icons/react";
import { ApiError, result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import type { Profile } from "../shell/presence";
import { ErrorNotice, TextField } from "../shell/ui";
import { useDraft } from "./composer";
import { MessageText } from "./message-text";
import { selectedSource } from "./comment-selection";
import styles from "./conversation.module.css";

type Target = Schema<"SavedOutputTarget">;
type Selection = Schema<"CommentSelection">;
export type CommentDraft = {
  publication: Schema<"CommentPublication">;
  text: string;
  status: "editing" | "pending" | "unknown" | "published";
  error?: unknown;
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
  begin: (target: Target, text: string, selection?: Selection) => void;
  browse: (target?: Target) => void;
  inspect: (target: Target) => void;
  comments: Schema<"OutputComment">[];
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
  const read = () => {
    if (truncated) return;
    const current = window.getSelection();
    if (
      !container.current ||
      !current ||
      !container.current.contains(current.anchorNode)
    )
      return;
    setSelection(selectedSource(container.current, text, current));
    setAmbiguous(
      !current.isCollapsed && !selectedSource(container.current, text, current),
    );
  };
  const matches = target
    ? (discussion?.comments.filter(
        (comment) => targetKey(comment.target) === targetKey(target),
      ) ?? [])
    : [];
  return (
    <div ref={container} onMouseUp={read} onKeyUp={read} onTouchEnd={read}>
      <MessageText text={text} selectable={!!target && !truncated} />
      <details className={styles.rawSource}>
        <summary>{truncated ? "Displayed excerpt" : "Original text"}</summary>
        <pre
          className={styles.code}
          data-source-start={truncated ? undefined : "0"}
          data-source-end={text.length}
        >
          {text}
        </pre>
      </details>
      {discussion && target && (
        <div className={styles.commentActions}>
          {truncated && (
            <>
              <small>
                This output is truncated. Select from the original source, not
                this excerpt.
              </small>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => discussion.inspect(target)}
              >
                View complete original
              </Button>
            </>
          )}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => discussion.begin(target, text)}
          >
            Comment
          </Button>
          {selection && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => discussion.begin(target, text, selection)}
            >
              Comment on selection
            </Button>
          )}
          {!!matches.length && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => discussion.browse(target)}
            >
              {matches.length} loaded{" "}
              {matches.length === 1 ? "comment" : "comments"}
            </Button>
          )}
          {ambiguous && (
            <small>
              This selection cannot map exactly to the source. Select in
              Original text, or comment on the whole block.
            </small>
          )}
        </div>
      )}
    </div>
  );
}

export function Discussion({
  threadId,
  profile,
  children,
}: {
  threadId: string;
  profile: Profile;
  children: ReactNode;
}) {
  const transport = useTransport();
  const queries = useQueryClient();
  const drafts = useContext(CommentDrafts);
  const composer = useDraft(threadId);
  const [, refresh] = useState(0);
  const update = () => refresh((value) => value + 1);
  const [open, setOpen] = useState(false);
  const [filter, setFilter] = useState<Target>();
  const [original, setOriginal] = useState<Target>();
  const [message, setMessage] = useState("");
  const draft = drafts.get(threadId);
  const comments = useInfiniteQuery({
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
              limit: 20,
            },
          },
          signal,
        }),
      ),
    getNextPageParam: (page) => page.next_cursor ?? undefined,
  });
  const rows = comments.data?.pages.flatMap((page) => page.comments) ?? [];
  const publish = async (current: CommentDraft) => {
    if (current.status === "pending" || current.status === "published") return;
    const uncertain = current.status === "unknown";
    current.status = "pending";
    current.error = undefined;
    update();
    try {
      const saved = await result(
        transport.client.POST("/api/threads/{thread_id}/comments", {
          params: { path: { thread_id: threadId } },
          body: current.publication,
        }),
      );
      if (saved.comment_id !== current.publication.comment_id)
        throw new Error(
          "Publication acknowledgement did not match this comment.",
        );
      current.status = "published";
      void queries.invalidateQueries({ queryKey: ["comments", threadId] });
    } catch (error) {
      current.status =
        error instanceof ApiError &&
        (error.code === "comment_target_stale" ||
          (!uncertain && error.status >= 400 && error.status < 500))
          ? "editing"
          : "unknown";
      current.error = error;
    } finally {
      update();
    }
  };
  const capture = useMutation({
    mutationFn: async (commentId: string) => {
      if (composer.doc.getMap("attachments").size >= 8)
        throw new Error(
          "Remove an attachment before adding feedback (limit: eight).",
        );
      const incarnation = composer.draftId;
      const attachment = await result(
        transport.client.POST(
          "/api/threads/{thread_id}/comments/{comment_id}/capture",
          {
            params: { path: { thread_id: threadId, comment_id: commentId } },
          },
        ),
      );
      if (incarnation !== composer.draftId || composer.replacement)
        throw new Error(
          "The shared draft changed during capture. Rejoin and add feedback explicitly.",
        );
      if (composer.doc.getMap("attachments").size >= 8)
        throw new Error(
          "The shared selection now has eight attachments. Remove one and add feedback again.",
        );
      queries.setQueryData(
        ["thread", threadId, "attachment", attachment.attachment_id],
        attachment,
      );
      composer.doc
        .getMap("attachments")
        .set(crypto.randomUUID(), attachment.attachment_id);
      setMessage(
        "Feedback added to the shared composer. Inspect its complete captured text before Send or Send as instruction.",
      );
    },
  });
  const begin = (target: Target, text: string, selection?: Selection) => {
    if (
      draft &&
      draft.status !== "published" &&
      (draft.publication.body || draft.status !== "editing")
    ) {
      setMessage(
        "Your private draft is retained. Finish or discard it before choosing a different target.",
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
    setOriginal(undefined);
    setOpen(true);
  };
  return (
    <DiscussionContext
      value={{
        begin,
        browse: (target) => {
          setFilter(target);
          setOpen(true);
        },
        comments: rows,
        inspect: setOriginal,
      }}
    >
      {children}
      <ModalFrame
        open={open}
        onOpenChange={setOpen}
        title="Saved output discussion"
        description="Comments are saved human feedback. They enter model input only when explicitly added and sent."
        closeLabel="Close"
      >
        <div className={styles.form}>
          {message && <p role="status">{message}</p>}
          {draft?.status === "published" && (
            <section className={styles.commentDraft}>
              <h3>Comment published</h3>
              <p>
                Saved with attribution to{" "}
                {draft.publication.author.display_name}. Publishing did not send
                model input.
              </p>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  drafts.delete(threadId);
                  update();
                }}
              >
                Done
              </Button>
            </section>
          )}
          {draft && draft.status !== "published" && (
            <section className={styles.commentDraft}>
              <h3>Private comment draft</h3>
              <small>
                Root {threadId} · output{" "}
                {draft.publication.target.source_id.slice(0, 12)}
              </small>
              <details>
                <summary>
                  {draft.publication.selection
                    ? "Exact selected quote"
                    : "Output preview · comment applies to the whole block"}
                </summary>
                <pre className={styles.code}>
                  {draft.publication.selection?.quote ?? draft.text}
                </pre>
              </details>
              <fieldset
                disabled={draft.status !== "editing"}
                className={styles.form}
              >
                <TextField
                  label="Author label (required)"
                  value={draft.publication.author.display_name}
                  onChange={(value) => {
                    draft.publication = {
                      ...draft.publication,
                      author: { display_name: value },
                    };
                    update();
                  }}
                />
                <label className={styles.form}>
                  Comment
                  <textarea
                    rows={4}
                    value={draft.publication.body}
                    onChange={(event) => {
                      draft.publication = {
                        ...draft.publication,
                        body: event.target.value,
                      };
                      update();
                    }}
                  />
                </label>
              </fieldset>
              <ErrorNotice error={draft.error} />
              <div className={styles.commentActions}>
                {draft.status === "editing" && (
                  <Button
                    disabled={
                      !draft.publication.body.trim() ||
                      [...draft.publication.body].length > 16384 ||
                      !draft.publication.author.display_name.trim() ||
                      [...draft.publication.author.display_name].length > 80
                    }
                    onClick={() => void publish(draft)}
                  >
                    Post comment
                  </Button>
                )}
                {draft.status === "pending" && <p role="status">Posting…</p>}
                {draft.status === "unknown" && (
                  <>
                    <p>
                      We could not confirm whether your comment was posted.
                      Check this comment again without creating a duplicate.
                    </p>
                    <Button onClick={() => void publish(draft)}>
                      Check comment status
                    </Button>
                  </>
                )}
                {draft.status === "editing" && (
                  <Button
                    variant="ghost"
                    onClick={() => {
                      if (
                        !draft.publication.body ||
                        window.confirm("Discard this private comment draft?")
                      ) {
                        drafts.delete(threadId);
                        update();
                      }
                    }}
                  >
                    Discard draft
                  </Button>
                )}
              </div>
            </section>
          )}
          <div className={styles.commentActions}>
            <h3>{filter ? "Comments on this output" : "All saved comments"}</h3>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void comments.refetch()}
            >
              Refresh comments
            </Button>
            {filter && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setFilter(undefined)}
              >
                Show all
              </Button>
            )}
          </div>
          <ErrorNotice error={comments.error || capture.error} />
          {comments.isPending && <p>Loading comments…</p>}
          {comments.isSuccess && !rows.length && (
            <p>No comments on this selection yet.</p>
          )}
          {rows.map((comment) => (
            <article key={comment.comment_id} className={styles.commentCard}>
              <header>
                <strong>{comment.author.display_name}</strong>
                <time dateTime={comment.created_at}>
                  {new Date(comment.created_at).toLocaleString("en-US")}
                </time>
              </header>
              {comment.selection && (
                <blockquote>{comment.selection.quote}</blockquote>
              )}
              <MessageText text={comment.body} />
              <small>
                {comment.target.location.kind === "child_text"
                  ? "Child output"
                  : "Root output"}{" "}
                · {comment.target.source_id.slice(0, 12)}
              </small>
              <div className={styles.commentActions}>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setOriginal(comment.target)}
                >
                  View original output
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setFilter(comment.target)}
                >
                  Filter this output
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={capture.isPending || !!composer.replacement}
                  onClick={() => capture.mutate(comment.comment_id)}
                >
                  Add feedback to prompt
                </Button>
              </div>
            </article>
          ))}
          {comments.hasNextPage && (
            <Button
              variant="outline"
              loading={comments.isFetchingNextPage}
              onClick={() => void comments.fetchNextPage()}
            >
              Load more comments
            </Button>
          )}
        </div>
      </ModalFrame>
      <ModalFrame
        open={!!original}
        onOpenChange={(value) => {
          if (!value) setOriginal(undefined);
        }}
        title="Original saved output"
        description="Read-only source inspection. This does not change the continuation."
        closeLabel="Close"
      >
        {original && <OriginalOutput threadId={threadId} target={original} />}
      </ModalFrame>
    </DiscussionContext>
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
      <small>
        {target.producing_thread_id} · {target.source_id}
      </small>
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
  const [open, setOpen] = useState(false);
  const output = useInfiniteQuery({
    queryKey: ["child-saved-output", threadId, executionId],
    enabled: open,
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
      <ErrorNotice error={output.error} />
      {output.isFetching && <p>Loading saved child output…</p>}
      {output.data?.pages
        .flatMap((page) => page.outputs)
        .map((item) => (
          <SavedOutput
            key={targetKey(item.target)}
            target={item.target}
            text={item.text}
          />
        ))}
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

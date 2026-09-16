// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TransportContext } from "../transport/context";
import { ApiError, type Schema, type Transport } from "../transport/client";
import { ChildSavedOutputs } from "./child-output";
import {
  CommentDrafts,
  Discussion,
  SavedOutput,
  CommentListButton,
  type CommentDraft,
} from "./comments";
import { MessageText } from "./message-text";
import { selectedSource } from "./comment-selection";
import { ComposerDrafts } from "./composer";
import { ThreadDraft, values } from "./draft";

const target: Schema<"SavedOutputTarget"> = {
  producing_thread_id: "thread-one",
  source_id: "a".repeat(64),
  location: { kind: "root_text", message: 0, part: 0 },
};
afterEach(() => {
  cleanup();
  window.getSelection()?.removeAllRanges();
});
function select(start: Node, from: number, end: Node, to: number) {
  const range = document.createRange();
  range.setStart(start, from);
  range.setEnd(end, to);
  const selection = window.getSelection()!;
  selection.removeAllRanges();
  selection.addRange(range);
  return selection;
}
it("maps repeated rendered text through exact source positions and Unicode code points", () => {
  const source = "A😀 **same** and **same**";
  const { container } = render(<MessageText text={source} selectable />);
  const nodes = container.querySelectorAll("strong span");
  expect(nodes).toHaveLength(2);
  const selection = select(nodes[1].firstChild!, 0, nodes[1].firstChild!, 4);
  const start = [...source.slice(0, source.lastIndexOf("same"))].length;
  expect(selectedSource(container, source, selection)).toEqual({
    start,
    end: start + 4,
    quote: "same",
  });
  const first = container.querySelector("p > span")!;
  expect(
    selectedSource(
      container,
      source,
      select(first.firstChild!, 1, first.firstChild!, 3),
    ),
  ).toEqual({ start: 1, end: 2, quote: "😀" });
});
it("maps cross-Markdown source spans but rejects decoded approximations and supports raw-source fallback", () => {
  const source = "Left **bold** &amp; right";
  const { container } = render(
    <>
      <MessageText text={source} selectable />
      <pre data-source-start="0">{source}</pre>
    </>,
  );
  const strong = container.querySelector("strong span")!;
  const left = container.querySelector("p > span")!;
  expect(
    selectedSource(
      container,
      source,
      select(left.firstChild!, 0, strong.firstChild!, 4),
    ),
  ).toEqual({ start: 0, end: 11, quote: "Left **bold" });
  const decoded = container.querySelector("p")!.lastChild!;
  expect(
    selectedSource(container, source, select(decoded, 1, decoded, 2)),
  ).toBeUndefined();
  const raw = container.querySelector("pre")!.firstChild!;
  expect(selectedSource(container, source, select(raw, 5, raw, 13))).toEqual({
    start: 5,
    end: 13,
    quote: "**bold**",
  });
});
function setup(
  POST: ReturnType<typeof vi.fn>,
  records: Schema<"OutputComment">[] = [],
  PATCH = vi.fn(),
  DELETE = vi.fn(),
) {
  const drafts = new Map<string, CommentDraft>();
  const composer = new ThreadDraft();
  composer.draftId = "draft-one";
  composer.doc.getText("text").insert(0, "My own prompt");
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const GET = vi.fn(
    async (
      path: string,
      options: { params: { path: { comment_id?: string } } },
    ) => ({
      data: path.endsWith("/{comment_id}")
        ? records.find(
            (record) => record.comment_id === options.params.path.comment_id,
          )
        : {
            comments: records.slice(0, 20),
            next_cursor: records.length >= 20 ? "next-page" : null,
          },
    }),
  );
  const onReferenceAdded = vi.fn();
  const tree = () => (
    <QueryClientProvider client={queries}>
      <TransportContext
        value={{ client: { GET, POST, PATCH, DELETE } } as unknown as Transport}
      >
        <ComposerDrafts value={new Map([["thread-one", composer]])}>
          <CommentDrafts value={drafts}>
            <Discussion
              threadId="thread-one"
              onReferenceAdded={onReferenceAdded}
              profile={{ display_name: "Reader", color: "#123456" }}
            >
              <CommentListButton />
              <SavedOutput target={target} text="Exact saved source" />
            </Discussion>
          </CommentDrafts>
        </ComposerDrafts>
      </TransportContext>
    </QueryClientProvider>
  );
  return { ...render(tree()), tree, drafts, composer, GET, onReferenceAdded };
}
it("retains a private comment when discard is cancelled and deletes only after confirmation", async () => {
  const { drafts } = setup(vi.fn());
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Add comment" }));
  fireEvent.change(
    await screen.findByLabelText("Comment", { selector: "textarea" }),
    { target: { value: "Keep my private comment" } },
  );
  await user.click(screen.getByRole("button", { name: "Discard draft" }));
  const dialog = await screen.findByRole("dialog", {
    name: "Discard this private comment draft?",
  });
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(drafts.get("thread-one")?.publication.body).toBe(
    "Keep my private comment",
  );
  await waitFor(() =>
    expect(
      screen.queryByRole("dialog", {
        name: "Discard this private comment draft?",
      }),
    ).toBeNull(),
  );
  await user.click(screen.getByRole("button", { name: "Discard draft" }));
  await user.click(
    within(
      await screen.findByRole("dialog", {
        name: "Discard this private comment draft?",
      }),
    ).getByRole("button", { name: "Discard draft" }),
  );
  expect(drafts.has("thread-one")).toBe(false);
});

it("discards an empty private comment without asking for confirmation", async () => {
  const { drafts } = setup(vi.fn());
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Add comment" }));
  await screen.findByLabelText("Comment", { selector: "textarea" });
  await user.click(screen.getByRole("button", { name: "Discard draft" }));
  expect(drafts.has("thread-one")).toBe(false);
  expect(
    screen.queryByRole("dialog", {
      name: "Discard this private comment draft?",
    }),
  ).toBeNull();
});

it("preserves a frozen publication after lost acknowledgement and reconciles the same identity", async () => {
  const POST = vi
    .fn()
    .mockRejectedValueOnce(new Error("Response lost"))
    .mockImplementation(async (_path, { body }) => ({
      data: {
        ...body,
        root_thread_id: "thread-one",
        created_at: "2026-09-12T00:00:00Z",
      },
    }));
  const { drafts, composer } = setup(POST);
  fireEvent.click(screen.getByRole("button", { name: "Add comment" }));
  fireEvent.change(
    await screen.findByLabelText("Comment", { selector: "textarea" }),
    { target: { value: "Preserve my full comment." } },
  );
  fireEvent.click(screen.getByRole("button", { name: "Post comment" }));
  await screen.findByRole("button", { name: "Check comment status" });
  expect(
    screen
      .getByLabelText("Comment", { selector: "textarea" })
      .matches(":disabled"),
  ).toBe(true);
  const identity = drafts.get("thread-one")!.publication.comment_id;
  fireEvent.click(screen.getByRole("button", { name: "Check comment status" }));
  await screen.findByText("Comment posted. Not sent to the agent.");
  expect(POST).toHaveBeenCalledTimes(2);
  expect(POST.mock.calls[0][1].body).toEqual(POST.mock.calls[1][1].body);
  expect(POST.mock.calls[1][1].body.comment_id).toBe(identity);
  expect(values(composer.doc)).toEqual({
    prompt: "My own prompt",
    attachment_ids: [],
  });
});
it("retains a private draft across navigation and adds only an explicit captured reference", async () => {
  const comment: Schema<"OutputComment"> = {
    comment_id: "comment-1234567890123456",
    target,
    author: { display_name: "Other reader" },
    body: "Full independent publication",
    root_thread_id: "thread-one",
    created_at: "2026-09-12T00:00:00Z",
  };
  const attachment: Schema<"ThreadAttachment"> = {
    attachment_id: "attachment-feedback",
    name: "Feedback.txt",
    media_type: "text/plain",
    size: 100,
    source: {
      kind: "comment_reference",
      root_thread_id: "thread-one",
      comment_id: comment.comment_id,
      target,
    },
  };
  const POST = vi.fn(async (_path: string) => ({ data: attachment }));
  const { tree, rerender, drafts, composer, onReferenceAdded } = setup(POST, [
    comment,
  ]);
  fireEvent.click(screen.getByRole("button", { name: "Add comment" }));
  fireEvent.change(
    await screen.findByLabelText("Comment", { selector: "textarea" }),
    { target: { value: "Unpublished private draft" } },
  );
  rerender(<div>Another route</div>);
  rerender(tree());
  fireEvent.click(screen.getByRole("button", { name: "Comments" }));
  expect(
    (
      (await screen.findByLabelText("Comment", {
        selector: "textarea",
      })) as HTMLTextAreaElement
    ).value,
  ).toBe("Unpublished private draft");
  expect(POST).not.toHaveBeenCalled();
  fireEvent.click(
    await screen.findByRole("button", { name: "Add to message" }),
  );
  await waitFor(() =>
    expect(values(composer.doc).attachment_ids).toEqual([
      attachment.attachment_id,
    ]),
  );
  expect(values(composer.doc).prompt).toBe("My own prompt");
  expect(drafts.get("thread-one")!.publication.body).toBe(
    "Unpublished private draft",
  );
  expect(POST.mock.calls[0][0]).toContain("/capture");
  expect(onReferenceAdded).toHaveBeenCalledOnce();
  await waitFor(() =>
    expect(screen.queryByRole("dialog", { name: "Comments" })).toBeNull(),
  );
});

it("keeps unknown attribution frozen on access failure but recovers a verified stale uncommitted target", async () => {
  const POST = vi
    .fn()
    .mockRejectedValueOnce(new Error("Response lost"))
    .mockRejectedValueOnce(new ApiError("Access expired", 401))
    .mockRejectedValueOnce(
      new ApiError("The original target changed", 409, "comment_target_stale"),
    );
  const { drafts } = setup(POST);
  fireEvent.click(screen.getByRole("button", { name: "Add comment" }));
  fireEvent.change(
    await screen.findByLabelText("Comment", { selector: "textarea" }),
    { target: { value: "Keep this private draft" } },
  );
  fireEvent.click(screen.getByRole("button", { name: "Post comment" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "Check comment status" }),
  );
  await screen.findByText("Access expired");
  expect(
    screen
      .getByLabelText("Comment", { selector: "textarea" })
      .matches(":disabled"),
  ).toBe(true);
  fireEvent.click(
    await screen.findByRole("button", { name: "Check comment status" }),
  );
  await screen.findByRole("button", { name: "Discard draft" });
  expect(
    screen
      .getByLabelText("Comment", { selector: "textarea" })
      .matches(":disabled"),
  ).toBe(false);
  expect(drafts.get("thread-one")!.publication.body).toBe(
    "Keep this private draft",
  );
  expect(
    new Set(POST.mock.calls.map((call) => call[1].body.comment_id)).size,
  ).toBe(1);
});
it("translates an exact original-output window selection without switching continuation", async () => {
  const comment: Schema<"OutputComment"> = {
    comment_id: "comment-1234567890123456",
    target: { ...target, source_id: "b".repeat(64) },
    author: { display_name: "Reader" },
    body: "Original output",
    root_thread_id: "thread-one",
    created_at: "2026-09-12T00:00:00Z",
  };
  const POST = vi.fn(async (_path: string) => ({
    data: {
      target,
      text: "😀 second window",
      offset: 65536,
      total_characters: 65551,
      next_offset: null,
    },
  }));
  const { drafts } = setup(POST, [comment]);
  fireEvent.click(screen.getByRole("button", { name: "Comments" }));
  fireEvent.click(await screen.findByRole("button", { name: "View response" }));
  const pre = await screen.findByText("😀 second window", { selector: "pre" });
  select(pre.firstChild!, 0, pre.firstChild!, 2);
  fireEvent.mouseUp(pre);
  fireEvent.click(
    await screen.findByRole("button", {
      name: "Comment on original selection",
    }),
  );
  await screen.findByRole("heading", { name: "New comment" });
  expect(drafts.get("thread-one")!.publication.selection).toEqual({
    start: 65536,
    end: 65537,
    quote: "😀",
  });
  expect(
    POST.mock.calls.every(
      (call) => call[0] === "/api/threads/{thread_id}/saved-output",
    ),
  ).toBe(true);
});
it("never anchors a server-truncated transcript excerpt", () => {
  const { container } = render(
    <SavedOutput
      target={target}
      text="prefix\n...[content truncated]"
      truncated
    />,
  );
  expect(screen.getByText("Displayed excerpt")).toBeTruthy();
  expect(container.querySelector("[data-source-start]")).toBeNull();
});

it("splits overlapping verified highlights at exact Unicode source offsets without relocating stale quotes", () => {
  const source = "A😀 **same** then **same**";
  const start = [...source.slice(0, source.lastIndexOf("same"))].length;
  const { container } = render(
    <MessageText
      text={source}
      selectable
      highlights={[
        { id: "second", selection: { start, end: start + 4, quote: "same" } },
        {
          id: "overlap",
          selection: { start: start + 2, end: start + 4, quote: "me" },
        },
        { id: "stale", selection: { start: 0, end: 4, quote: "same" } },
      ]}
    />,
  );
  const marks = container.querySelectorAll<HTMLElement>("[data-comment-ids]");
  expect([...marks].map((mark) => mark.textContent).join("")).toBe("same");
  expect(marks[0].dataset.commentIds).toBe("second");
  expect(marks[1].dataset.commentIds).toBe("second overlap");
  expect(
    container.querySelector("strong")?.querySelector("[data-comment-ids]"),
  ).toBeNull();
  expect(
    selectedSource(
      container,
      source,
      select(marks[0].firstChild!, 0, marks[1].firstChild!, 2),
    ),
  ).toEqual({ start, end: start + 4, quote: "same" });
});
it("opens a text selection's private editor inline and publishes only after explicit confirmation", async () => {
  const POST = vi.fn(async (_path, { body }) => ({
    data: {
      ...body,
      root_thread_id: "thread-one",
      created_at: "2026-09-14T00:00:00Z",
    },
  }));
  const { container, drafts } = setup(POST);
  const source = container.querySelector("[data-source-start]")!;
  select(source.firstChild!, 6, source.firstChild!, 11);
  fireEvent.mouseUp(source);
  fireEvent.click(screen.getByRole("button", { name: "Comment on selection" }));
  await screen.findByLabelText("Comment", { selector: "textarea" });
  expect(
    screen
      .getByRole("dialog", { name: "Comments" })
      .hasAttribute("data-a13n-modal"),
  ).toBe(false);
  expect(drafts.get("thread-one")!.publication.selection).toEqual({
    start: 6,
    end: 11,
    quote: "saved",
  });
  expect(POST).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Comment", { selector: "textarea" }), {
    target: { value: "Inline private feedback" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Post comment" }));
  await screen.findByText("Comment posted. Not sent to the agent.");
  expect(POST.mock.calls[0][1].body.selection).toEqual({
    start: 6,
    end: 11,
    quote: "saved",
  });
});
it("activates a saved text highlight from the keyboard without creating or sending feedback", async () => {
  const POST = vi.fn();
  setup(POST, [
    {
      comment_id: "comment-highlight",
      target,
      author: { display_name: "Reader" },
      body: "A nearby discussion",
      selection: { start: 6, end: 11, quote: "saved" },
      root_thread_id: "thread-one",
      created_at: "2026-09-14T00:00:00Z",
    },
  ]);
  const mark = await screen.findByRole("button", {
    name: "Read comments on highlighted text",
  });
  fireEvent.keyDown(mark, { key: "Enter" });
  await screen.findByText("A nearby discussion");
  expect(
    screen.queryByLabelText("Comment", { selector: "textarea" }),
  ).toBeNull();
  expect(POST).not.toHaveBeenCalled();
});

it("reads the latest child result inline and joins exact-source windows without an event log", async () => {
  const target: Schema<"SavedOutputTarget"> = {
    producing_thread_id: "child-one",
    source_id: "b".repeat(64),
    location: { kind: "child_text", execution_id: "exec-one", activity: null },
  };
  const first = {
    target,
    text: "First paragraph.\n\n",
    offset: 0,
    total_characters: 38,
    next_offset: 18,
  };
  const GET = vi.fn(async () => ({ data: { outputs: [first] } }));
  const POST = vi.fn(async () => ({
    data: {
      target,
      text: "Final paragraph.",
      offset: 18,
      total_characters: 34,
      next_offset: null,
    },
  }));
  const queries = new QueryClient();
  render(
    <QueryClientProvider client={queries}>
      <TransportContext
        value={{ client: { GET, POST } } as unknown as Transport}
      >
        <ChildSavedOutputs
          threadId="parent"
          rootThreadId="root"
          executionId="exec-one"
          fallback={<p>Waiting for saved output</p>}
        />
      </TransportContext>
    </QueryClientProvider>,
  );
  await screen.findByText("First paragraph.");
  expect(screen.queryByText("Saved child output and comments")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Load more result" }));
  await screen.findByText("Final paragraph.");
  expect(POST.mock.calls[0]).toEqual([
    "/api/threads/{thread_id}/saved-output",
    expect.objectContaining({
      params: { path: { thread_id: "root" }, query: { offset: 18 } },
      body: target,
    }),
  ]);
  expect(screen.queryByRole("button", { name: "Load more result" })).toBeNull();
});

it("keeps observed output until a saved child result becomes available", async () => {
  const GET = vi
    .fn()
    .mockRejectedValueOnce(
      new ApiError("No checkpoint", 400, "comment_source_unavailable"),
    )
    .mockResolvedValue({ data: { outputs: [] } });
  const queries = new QueryClient();
  render(
    <QueryClientProvider client={queries}>
      <TransportContext value={{ client: { GET } } as unknown as Transport}>
        <ChildSavedOutputs
          threadId="root"
          rootThreadId="root"
          executionId="exec-one"
          fallback={<p>Observed answer</p>}
        />
      </TransportContext>
    </QueryClientProvider>,
  );
  await waitFor(() => expect(GET).toHaveBeenCalledTimes(1));
  expect(screen.getByText("Observed answer")).toBeTruthy();
  expect(screen.queryByRole("alert")).toBeNull();
  await queries.invalidateQueries({
    queryKey: ["child-saved-output", "root", "exec-one"],
  });
  await waitFor(() => expect(GET).toHaveBeenCalledTimes(2));
  expect(screen.getByText("Observed answer")).toBeTruthy();
});

const savedComment = (): Schema<"OutputComment"> => ({
  comment_id: "comment-edit-1234567890",
  target,
  author: { display_name: "Reader" },
  body: "Review this response",
  selection: { start: 6, end: 11, quote: "saved" },
  root_thread_id: "thread-one",
  created_at: "2026-09-14T00:00:00Z",
  version: 1,
});

it("edits only the body, renders the saved comment immediately, and never captures implicitly", async () => {
  const records = [savedComment()];
  const POST = vi.fn();
  const PATCH = vi.fn(async (_path, { body }) => {
    records[0] = {
      ...records[0],
      body: body.body,
      version: 2,
      updated_at: "2026-09-15T00:00:00Z",
    };
    return { data: records[0] };
  });
  const { drafts, composer } = setup(POST, records, PATCH);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Comments" }));
  await user.click(
    await screen.findByRole("button", { name: "Comment actions" }),
  );
  await user.click(
    await screen.findByRole("menuitem", { name: "Edit comment" }),
  );
  fireEvent.change(
    await screen.findByLabelText("Comment", { selector: "textarea" }),
    { target: { value: "Updated feedback" } },
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByText("Updated feedback");
  expect(PATCH.mock.calls[0][1].body).toEqual({
    body: "Updated feedback",
    expected_version: 1,
  });
  expect(drafts.size).toBe(0);
  expect(screen.queryByRole("button", { name: "Done" })).toBeNull();
  expect(POST).not.toHaveBeenCalled();
  expect(values(composer.doc).attachment_ids).toEqual([]);
});

it("retries an uncertain edit with the same version and frozen body", async () => {
  const records = [savedComment()];
  const PATCH = vi
    .fn()
    .mockRejectedValueOnce(new Error("Response lost"))
    .mockImplementation(async (_path, { body }) => {
      records[0] = { ...records[0], body: body.body, version: 2 };
      return { data: records[0] };
    });
  setup(vi.fn(), records, PATCH);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Comments" }));
  await user.click(
    await screen.findByRole("button", { name: "Comment actions" }),
  );
  await user.click(
    await screen.findByRole("menuitem", { name: "Edit comment" }),
  );
  fireEvent.change(
    await screen.findByLabelText("Comment", { selector: "textarea" }),
    { target: { value: "Retry safely" } },
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByRole("button", { name: "Check comment status" });
  expect(
    (
      screen.getByLabelText("Comment", {
        selector: "textarea",
      }) as HTMLTextAreaElement
    ).disabled,
  ).toBe(true);
  await user.click(
    screen.getByRole("button", { name: "Check comment status" }),
  );
  await screen.findByText("Retry safely");
  expect(PATCH.mock.calls[0][1].body).toEqual(PATCH.mock.calls[1][1].body);
});

it("confirms deletion, removes its highlight, and leaves an existing composer reference alone", async () => {
  const records = [savedComment()];
  const DELETE = vi.fn(async (_path: string, _options: unknown) => {
    records.splice(0);
    return { data: undefined };
  });
  const { composer } = setup(vi.fn(), records, vi.fn(), DELETE);
  composer.addAttachment("attachment-previous-capture");
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Comments" }));
  await user.click(
    await screen.findByRole("button", { name: "Comment actions" }),
  );
  await user.click(
    await screen.findByRole("menuitem", { name: "Delete comment…" }),
  );
  const dialog = await screen.findByRole("dialog", {
    name: "Delete this comment?",
  });
  expect(DELETE).not.toHaveBeenCalled();
  await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
  expect(DELETE).not.toHaveBeenCalled();
  await waitFor(() =>
    expect(
      screen.queryByRole("dialog", { name: "Delete this comment?" }),
    ).toBeNull(),
  );
  await user.click(screen.getByRole("button", { name: "Comment actions" }));
  await user.click(
    await screen.findByRole("menuitem", { name: "Delete comment…" }),
  );
  await user.click(
    within(
      await screen.findByRole("dialog", { name: "Delete this comment?" }),
    ).getByRole("button", { name: "Delete comment" }),
  );
  await screen.findByText(
    "Comment deleted. Existing message references are unchanged.",
  );
  await waitFor(() =>
    expect(
      screen.queryByRole("button", {
        name: "Read comments on highlighted text",
      }),
    ).toBeNull(),
  );
  expect(DELETE.mock.calls[0][1]).toMatchObject({
    params: { query: { expected_version: 1 } },
  });
  expect(values(composer.doc).attachment_ids).toEqual([
    "attachment-previous-capture",
  ]);
});

it("keeps capture failures in discussion without touching the draft or transferring focus", async () => {
  const POST = vi
    .fn()
    .mockRejectedValue(
      new ApiError("The comment changed", 409, "comment_version_conflict"),
    );
  const { composer, onReferenceAdded } = setup(POST, [savedComment()]);
  fireEvent.click(screen.getByRole("button", { name: "Comments" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "Add to message" }),
  );
  await screen.findByText("The comment changed");
  expect(screen.getByRole("dialog", { name: "Comments" })).toBeTruthy();
  expect(values(composer.doc)).toEqual({
    prompt: "My own prompt",
    attachment_ids: [],
  });
  expect(onReferenceAdded).not.toHaveBeenCalled();
  expect(POST.mock.calls[0][1].params.query).toEqual({ expected_version: 1 });
});

it("narrows an activated highlight to exactly its matching comments", async () => {
  setup(vi.fn(), [
    savedComment(),
    {
      ...savedComment(),
      comment_id: "comment-other",
      body: "Another passage",
      selection: { start: 12, end: 18, quote: "source" },
    },
  ]);
  const highlights = await screen.findAllByRole("button", {
    name: "Read comments on highlighted text",
  });
  fireEvent.keyDown(highlights[0], { key: "Enter" });
  await screen.findByText("Review this response");
  expect(screen.queryByText("Another passage")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "All comments" }));
  await screen.findByText("Another passage");
});

it("reviews the latest version on conflict while preserving the user's edit for explicit save", async () => {
  const records = [savedComment()];
  const PATCH = vi
    .fn()
    .mockImplementationOnce(async () => {
      records[0] = {
        ...records[0],
        version: 2,
        body: "A collaborator's revision",
      };
      throw new ApiError(
        "This comment changed",
        409,
        "comment_version_conflict",
      );
    })
    .mockImplementation(async (_path, { body }) => {
      records[0] = { ...records[0], version: 3, body: body.body };
      return { data: records[0] };
    });
  const { drafts } = setup(vi.fn(), records, PATCH);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Comments" }));
  await user.click(
    await screen.findByRole("button", { name: "Comment actions" }),
  );
  await user.click(
    await screen.findByRole("menuitem", { name: "Edit comment" }),
  );
  fireEvent.change(
    await screen.findByLabelText("Comment", { selector: "textarea" }),
    { target: { value: "My careful edit" } },
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByText("A collaborator's revision");
  expect(
    (screen.getByRole("button", { name: "Save changes" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(drafts.get("thread-one")?.publication.body).toBe("My careful edit");
  await user.click(
    screen.getByRole("button", { name: "Continue with my edit" }),
  );
  expect(PATCH).toHaveBeenCalledOnce();
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await screen.findByText("My careful edit");
  expect(PATCH.mock.calls[1][1].body).toEqual({
    body: "My careful edit",
    expected_version: 2,
  });
});

it("keeps the nearby source anchor and shows a new publication on a full first page", async () => {
  const records = Array.from({ length: 20 }, (_, index) => ({
    ...savedComment(),
    comment_id: `comment-${index}`,
    body: `Previous comment ${index}`,
    selection: null,
  }));
  const POST = vi.fn(async (_path, { body }) => {
    const saved = {
      ...body,
      version: 1,
      created_at: "2026-09-15T00:00:00Z",
      root_thread_id: "thread-one",
    };
    records.unshift(saved);
    return { data: saved };
  });
  const { GET } = setup(POST, records);
  const user = userEvent.setup();
  const source = await screen.findByRole("button", { name: "View comments" });
  await user.click(source);
  const dialog = await screen.findByRole("dialog", { name: "Comments" });
  await user.click(within(dialog).getByRole("button", { name: "Add comment" }));
  fireEvent.change(screen.getByLabelText("Comment", { selector: "textarea" }), {
    target: { value: "Newest feedback" },
  });
  await user.click(screen.getByRole("button", { name: "Post comment" }));
  await screen.findByText("Newest feedback");
  expect(GET).toHaveBeenCalledWith(
    "/api/threads/{thread_id}/comments",
    expect.objectContaining({
      params: expect.objectContaining({
        query: expect.objectContaining({ newest_first: true }),
      }),
    }),
  );
  await user.click(screen.getByRole("button", { name: "Close comments" }));
  await waitFor(() => expect(document.activeElement).toBe(source));
});

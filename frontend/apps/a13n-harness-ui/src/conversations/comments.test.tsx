// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TransportContext } from "../transport/context";
import { ApiError, type Schema, type Transport } from "../transport/client";
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
it("rejects decoded or cross-Markdown approximations and supports exact raw-source fallback", () => {
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
  ).toBeUndefined();
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
) {
  const drafts = new Map<string, CommentDraft>();
  const composer = new ThreadDraft();
  composer.draftId = "draft-one";
  composer.doc.getText("text").insert(0, "My own prompt");
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const GET = vi.fn(async () => ({
    data: { comments: records, next_cursor: null },
  }));
  const tree = () => (
    <QueryClientProvider client={queries}>
      <TransportContext
        value={{ client: { GET, POST } } as unknown as Transport}
      >
        <ComposerDrafts value={new Map([["thread-one", composer]])}>
          <CommentDrafts value={drafts}>
            <Discussion
              threadId="thread-one"
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
  return { ...render(tree()), tree, drafts, composer, GET };
}
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
  fireEvent.click(screen.getByRole("button", { name: "Comment" }));
  fireEvent.change(
    await screen.findByLabelText("Comment", { selector: "textarea" }),
    { target: { value: "Preserve my full comment." } },
  );
  fireEvent.click(screen.getByRole("button", { name: "Publish comment" }));
  await screen.findByRole("button", { name: "Reconcile publication" });
  expect(
    screen
      .getByLabelText("Comment", { selector: "textarea" })
      .matches(":disabled"),
  ).toBe(true);
  const identity = drafts.get("thread-one")!.publication.comment_id;
  fireEvent.click(
    screen.getByRole("button", { name: "Reconcile publication" }),
  );
  await screen.findByRole("heading", { name: "Comment published" });
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
  const { tree, rerender, drafts, composer } = setup(POST, [comment]);
  fireEvent.click(screen.getByRole("button", { name: "Comment" }));
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
    await screen.findByRole("button", { name: "Add feedback to prompt" }),
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
  fireEvent.click(screen.getByRole("button", { name: "Comment" }));
  fireEvent.change(
    await screen.findByLabelText("Comment", { selector: "textarea" }),
    { target: { value: "Keep this private draft" } },
  );
  fireEvent.click(screen.getByRole("button", { name: "Publish comment" }));
  fireEvent.click(
    await screen.findByRole("button", { name: "Reconcile publication" }),
  );
  await screen.findByText("Access expired");
  expect(
    screen
      .getByLabelText("Comment", { selector: "textarea" })
      .matches(":disabled"),
  ).toBe(true);
  fireEvent.click(
    await screen.findByRole("button", { name: "Reconcile publication" }),
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
    target,
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
  fireEvent.click(
    await screen.findByRole("button", { name: "View original output" }),
  );
  const pre = await screen.findByText("😀 second window", { selector: "pre" });
  select(pre.firstChild!, 0, pre.firstChild!, 2);
  fireEvent.mouseUp(pre);
  fireEvent.click(
    await screen.findByRole("button", {
      name: "Comment on original selection",
    }),
  );
  await screen.findByRole("heading", { name: "Private comment draft" });
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

// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { EditorView } from "codemirror";
import { TransportContext } from "../transport/context";
import type { Schema, Transport } from "../transport/client";
import { ComposerDrafts } from "../conversations/composer";
import { ThreadDraft } from "../conversations/draft";
import { PatchEditor } from "./patch-editor";

beforeEach(() => {
  Object.defineProperty(Range.prototype, "getClientRects", {
    configurable: true,
    value: () => [],
  });
  Object.defineProperty(Range.prototype, "getBoundingClientRect", {
    configurable: true,
    value: () => ({
      left: 0,
      top: 0,
      right: 0,
      bottom: 0,
      width: 0,
      height: 0,
    }),
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});
it("keeps editor selection in exact patch coordinates and resets it for a replacement revision", async () => {
  const text =
    "diff --git a/file b/file\n--- a/file\n+++ b/file\n@@ -40 +50 @@\n-old\n+new\n";
  const value: Schema<"GitDiff"> = {
    repository: {
      root: "/fixture",
      git_dir: "/fixture/.git",
      common_dir: "/fixture/.git",
      head_oid: "head",
      branch: "main",
    },
    path: "file",
    comparison: "staged",
    revision: "reviewed",
    index_revision: "index",
    presentation: "text",
    text,
  };
  const post = vi.fn(async () => ({
    data: { attachment: { attachment_id: "attachment-patch" } },
  }));
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const draft = new ThreadDraft();
  draft.draftId = "draft-one";
  vi.spyOn(draft, "synchronized", "get").mockReturnValue(true);
  const tree = (revision: string) => (
    <QueryClientProvider client={queries}>
      <TransportContext
        value={{ client: { POST: post } } as unknown as Transport}
      >
        <ComposerDrafts value={new Map([["thread-one", draft]])}>
          <PatchEditor
            key={revision}
            value={{ ...value, revision }}
            threadId="thread-one"
            disabled={false}
          />
        </ComposerDrafts>
      </TransportContext>
    </QueryClientProvider>
  );
  const view = render(tree("reviewed"));
  const editor = EditorView.findFromDOM(
    await screen.findByLabelText(
      "Git patch with old and new file line numbers",
    ),
  )!;
  expect(editor.state.doc.toString()).toBe(text);
  expect(editor.state.readOnly).toBe(true);
  act(() =>
    editor.dispatch({
      selection: {
        anchor: editor.state.doc.line(5).from,
        head: editor.state.doc.line(6).to,
      },
    }),
  );
  expect(
    screen.getByText("Selected patch lines 5–6 (including headers)"),
  ).toBeTruthy();
  fireEvent.click(
    screen.getByRole("button", { name: "Add selection to message" }),
  );
  await waitFor(() => expect(post).toHaveBeenCalledOnce());
  expect(post.mock.calls[0]).toEqual([
    "/api/threads/{thread_id}/host-git-captures",
    {
      params: { path: { thread_id: "thread-one" } },
      body: {
        repository_path: "/fixture",
        path: "file",
        comparison: "staged",
        expected_revision: "reviewed",
        start_line: 5,
        end_line: 6,
      },
    },
  ]);
  view.rerender(tree("replacement"));
  expect(
    screen.queryByRole("button", { name: "Add selection to message" }),
  ).toBeNull();
});

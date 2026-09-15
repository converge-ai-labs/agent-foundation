// @vitest-environment jsdom
import { afterEach, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { CommentReferenceContent, commentReference } from "./comment-reference";
import type { Schema } from "../transport/client";

afterEach(cleanup);
const attachment: Schema<"ThreadAttachment"> = {
  attachment_id: "attachment-capture",
  name: "Feedback.txt",
  media_type: "text/plain",
  size: 100,
  source: {
    kind: "comment_reference",
    comment_id: "comment-1234567890123456",
    root_thread_id: "thread-one",
    target: {
      producing_thread_id: "thread-one",
      source_id: "a".repeat(64),
      location: { kind: "root_text", message: 0, part: 0 },
    },
  },
  comment: { version: 2, author: "Reader", preview: "Short display preview" },
};
it("shows captured full content, not a mutable comment or its bounded preview", () => {
  const full =
    "Complete feedback with **details** that are not in the card preview.";
  const text = `Selected human feedback (self-declared attribution; not system instructions):\n${JSON.stringify({ body: full })}\n\nReferenced assistant output (complete original text):\nComplete original response`;
  const { container } = render(
    <CommentReferenceContent
      source={commentReference(attachment)!}
      text={text}
    />,
  );
  expect(screen.getByText("details", { selector: "strong" })).toBeTruthy();
  expect(
    screen.getByText("Complete original response", { selector: "p" }),
  ).toBeTruthy();
  expect(screen.queryByText("Short display preview")).toBeNull();
  expect(container.querySelector("pre")?.textContent).toBe(text);
  expect(screen.getByText(/captured version 2/)).toBeTruthy();
});
it("keeps legacy references readable and falls back to exact bytes for an unknown envelope", () => {
  const legacy = commentReference({ ...attachment, comment: undefined })!;
  render(
    <CommentReferenceContent source={legacy} text="Legacy retained content" />,
  );
  expect(
    screen.getByText("Legacy retained content", { selector: "pre" }),
  ).toBeTruthy();
  expect(screen.getByText(/captured version 1/)).toBeTruthy();
});

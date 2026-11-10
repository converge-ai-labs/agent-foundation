import { EditorState, Text } from "@codemirror/state";
import { expect, it } from "vitest";
import { sourceLines, textFormat } from "./text-model";

it.each([
  "",
  "alpha\n\nbeta\n",
  "alpha\n\nbeta",
  "\n",
  "alpha\n\n",
  "alpha\r\n\r\nbeta\r\n",
  "mixed\r\nend\n",
  "\tindented  \n    \nlast line",
])("round-trips the complete source through the reader model: %j", (text) => {
  const state = EditorState.create({
    doc: Text.of(sourceLines(text)),
    extensions: EditorState.lineSeparator.of("\n"),
  });
  expect(state.sliceDoc()).toBe(text);
});
it("distinguishes newline metadata without rewriting content", () => {
  expect(textFormat("a\r\n")).toEqual({ endings: "CRLF", finalNewline: true });
  expect(textFormat("a\r\nb\n")).toEqual({
    endings: "Mixed",
    finalNewline: true,
  });
  expect(textFormat("a\nlast")).toEqual({ endings: "LF", finalNewline: false });
  expect(textFormat("")).toEqual({ endings: "None", finalNewline: false });
});

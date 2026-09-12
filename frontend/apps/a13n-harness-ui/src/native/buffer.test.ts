import { expect, it } from "vitest";
import {
  basename,
  breadcrumbs,
  FileBuffer,
  joinPath,
  lineRange,
  parentPath,
} from "./buffer";
import { changeAxes } from "./changes";
import type { Schema } from "../transport/client";
function file(text = "base", revision = "one"): Schema<"FileText"> {
  return {
    resolved_path: "/code/file",
    presentation: "text",
    text,
    entry: {
      path: "/code/file",
      revision,
      kind: "file",
      size: text.length,
      modified_ns: 0,
      mode: 0,
    },
  };
}
it("refreshes clean text but preserves dirty private text and exact base revision", () => {
  const buffer = new FileBuffer(file());
  buffer.observe(file("fresh", "two"));
  expect(buffer.value).toBe("fresh");
  buffer.value = "private";
  buffer.observe(file("external", "three"));
  expect(buffer.value).toBe("private");
  expect(buffer.base.entry.revision).toBe("two");
  expect(buffer.conflict).toBe(true);
  buffer.adopt(true);
  expect(buffer.base.entry.revision).toBe("three");
  expect(buffer.value).toBe("private");
  expect(buffer.dirty).toBe(true);
  buffer.adopt(false);
  expect(buffer.value).toBe("external");
  expect(buffer.dirty).toBe(false);
});
it("save acknowledgement does not mark typing during the write as saved", () => {
  const buffer = new FileBuffer(file());
  buffer.value = "submitted";
  buffer.saving = true;
  buffer.value = "submitted plus newer typing";
  buffer.observe(file("old read during write", "two"));
  buffer.saved(file("submitted", "three").entry, "submitted");
  expect(buffer.base.text).toBe("submitted");
  expect(buffer.value).toBe("submitted plus newer typing");
  expect(buffer.dirty).toBe(true);
});
it("unknown writes require explicit adoption even when a clean buffer observes disk", () => {
  const buffer = new FileBuffer(file());
  buffer.uncertain = true;
  buffer.observe(file("external", "two"));
  expect(buffer.value).toBe("base");
  expect(buffer.uncertain).toBe(true);
  buffer.adopt(false);
  expect(buffer.uncertain).toBe(false);
  expect(buffer.value).toBe("external");
});
it("native paths retain POSIX backslashes, Windows drive roots and UNC shares", () => {
  expect(parentPath("/one/two/")).toBe("/one");
  expect(parentPath("/")).toBe("/");
  expect(basename("/one/a\\b")).toBe("a\\b");
  expect(parentPath("C:\\code\\file.txt")).toBe("C:\\code");
  expect(parentPath("C:\\code")).toBe("C:\\");
  expect(parentPath("C:\\")).toBe("C:\\");
  expect(parentPath("\\\\server\\share\\dir")).toBe("\\\\server\\share\\");
  expect(parentPath("\\\\server\\share\\")).toBe("\\\\server\\share\\");
  expect(joinPath("C:\\", "file.txt")).toBe("C:\\file.txt");
  expect(breadcrumbs("/one/two")).toEqual(["/", "/one", "/one/two"]);
});
it("range capture requires paired inclusive integer endpoints", () => {
  expect(() => lineRange("", "")).toThrow();
  expect(lineRange("2", "3")).toEqual({ start_line: 2, end_line: 3 });
  for (const [a, b] of [
    ["1", ""],
    ["0", "2"],
    ["3", "2"],
    ["1.5", "3"],
  ])
    expect(() => lineRange(a, b)).toThrow();
});
it("a tracked file can appear on both axes; ignored and untracked are not tracked diffs", () => {
  const change: Schema<"GitChange"> = {
    path: "file",
    kind: "tracked",
    index_status: "M",
    worktree_status: "M",
  };
  expect(changeAxes(change)).toEqual(["staged", "unstaged"]);
  expect(changeAxes({ ...change, kind: "untracked" })).toEqual(["untracked"]);
  expect(changeAxes({ ...change, kind: "ignored" })).toEqual([]);
  expect(changeAxes({ ...change, worktree_status: "." })).toEqual(["staged"]);
});

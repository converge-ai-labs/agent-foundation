import { expect, it } from "vitest";
import { linkedHostFile, nativeLink, pageLink } from "./page-links";
import type { Schema } from "../transport/client";

it("round-trips exact native paths, Git axes and terminal identity without keys or navigation commands", () => {
  const file: Schema<"PageFocus"> = {
    root_thread_id: "thread-a",
    target: { kind: "file", path: "C:\\code\\a + ?#界.txt" },
  };
  const url = new URL(pageLink(file)!, "https://example.invalid");
  expect(url.pathname).toBe("/threads/thread-a");
  expect(nativeLink(url.search)).toMatchObject({
    pane: "files",
    path: file.target.kind === "file" && file.target.path,
  });
  const diff = new URL(
    pageLink({
      target: {
        kind: "changes",
        repository_root: "/code/repo",
        path: "src/with # space.ts",
        comparison: "staged",
      },
    })!,
    "https://example.invalid",
  );
  expect(nativeLink(diff.search)).toMatchObject({
    pane: "changes",
    path: "/code/repo",
    diffPath: "src/with # space.ts",
    comparison: "staged",
  });
  expect(nativeLink("?terminal=terminal-live").terminal).toBe("terminal-live");
  expect(nativeLink("?native=execute&comparison=discard")).toMatchObject({
    pane: null,
    comparison: null,
  });
  expect(
    pageLink({
      target: {
        kind: "resource",
        resource_kind: "agent",
        resource_id: "missing",
      },
    }),
  ).toBeNull();
});

it("recognizes only same-instance Host file views as in-page file links", () => {
  const current = "https://harness.example/threads/thread-a";
  expect(
    linkedHostFile(
      "/threads/thread-a?native=files&native_path=%2Ftmp%2Freport.md",
      current,
    ),
  ).toBe("/tmp/report.md");
  expect(
    linkedHostFile(
      "https://harness.example/?native=files&native_path=C%3A%5Ccode%5Cnotes.md",
      current,
    ),
  ).toBe("C:\\code\\notes.md");
  expect(
    linkedHostFile(
      "https://other.example/threads/thread-a?native=files&native_path=%2Ftmp%2Freport.md",
      current,
    ),
  ).toBeNull();
  expect(
    linkedHostFile(
      "/settings?native=files&native_path=%2Ftmp%2Freport.md",
      current,
    ),
  ).toBeNull();
  expect(
    linkedHostFile(
      "/threads/thread-a?native=changes&native_path=%2Ftmp%2Frepo",
      current,
    ),
  ).toBeNull();
  expect(
    linkedHostFile(
      "/threads/thread-a?native=files&native_path=relative.md",
      current,
    ),
  ).toBeNull();
});

import { expect, it } from "vitest";
import { nativeLink, pageLink } from "./page-links";
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

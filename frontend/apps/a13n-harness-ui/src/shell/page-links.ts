import type { Schema } from "../transport/client";

export function pageLink(
  focus: Schema<"PageFocus">,
  sources: Schema<"ConfigurationSourceInfo">[] = [],
): string | null {
  const target = focus.target;
  const root = focus.root_thread_id
    ? `/threads/${encodeURIComponent(focus.root_thread_id)}`
    : "/";
  const query = new URLSearchParams();
  switch (target.kind) {
    case "conversation":
      return `/threads/${encodeURIComponent(target.thread_id)}`;
    case "project":
      return `/projects/${encodeURIComponent(target.project_id)}`;
    case "resource": {
      const source = sources.find(
        (item) =>
          item.resource_kind === target.resource_kind &&
          item.resource_ids.includes(target.resource_id),
      );
      return source
        ? `/settings/source?path=${encodeURIComponent(source.relative_path)}`
        : null;
    }
    case "workbench":
      return target.section === "home"
        ? "/"
        : target.section === "catalog"
          ? "/settings/catalog"
          : "/settings/resources";
    case "file":
      query.set("native", "files");
      query.set("native_path", target.path);
      break;
    case "changes":
      query.set("native", "changes");
      query.set("native_path", target.repository_root);
      if (target.path && target.comparison) {
        query.set("diff_path", target.path);
        query.set("comparison", target.comparison);
      }
      break;
    case "terminal":
      query.set("terminal", target.terminal_id);
      break;
  }
  return `${root}?${query}`;
}
export function nativeLink(search: string): {
  pane: "files" | "changes" | null;
  path: string;
  diffPath: string;
  comparison: "staged" | "unstaged" | "untracked" | null;
  terminal: string;
} {
  const query = new URLSearchParams(search);
  const pane = query.get("native");
  const comparison = query.get("comparison");
  return {
    pane: pane === "files" || pane === "changes" ? pane : null,
    path: query.get("native_path") || "",
    diffPath: query.get("diff_path") || "",
    comparison:
      comparison === "staged" ||
      comparison === "unstaged" ||
      comparison === "untracked"
        ? comparison
        : null,
    terminal: query.get("terminal") || "",
  };
}

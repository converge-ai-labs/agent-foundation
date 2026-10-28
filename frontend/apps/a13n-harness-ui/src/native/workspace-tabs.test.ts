import { expect, it } from "vitest";
import { emptyTabs, workspaceTabs, type WorkspaceTab } from "./workspace-tabs";

const file: WorkspaceTab = {
  id: "first",
  kind: "file",
  path: "/first.txt",
  root: "/",
};
const second: WorkspaceTab = {
  id: "second",
  kind: "file",
  path: "/second.txt",
  root: "/",
};

it("replaces a new tab with its selected content and keeps other file tabs", () => {
  let state = workspaceTabs(emptyTabs("new"), {
    type: "open",
    tab: file,
    replace: true,
  });
  expect(state.items).toEqual([{ ...file, id: "new" }]);
  state = workspaceTabs(state, { type: "open", tab: second, replace: false });
  expect(state.items.map((tab) => tab.kind)).toEqual(["file", "file"]);
  expect(state.active).toBe("second");
});

it("selects an already open file and removes only the replaced picker", () => {
  const state = {
    items: [file, second, { id: "new", kind: "new" } as const],
    active: "new",
  };
  const selected = workspaceTabs(state, {
    type: "open",
    tab: { ...file, id: "unused" },
    replace: true,
  });
  expect(selected.items).toEqual([file, second]);
  expect(selected.active).toBe(file.id);
});

it.each([
  ["browser", false],
  ["browser", true],
  ["terminal", false],
  ["terminal", true],
] as const)(
  "reuses the sole %s tab and retains the initiating new tab with replace=%s",
  (kind, replace) => {
    const tab: WorkspaceTab = { id: "existing", kind };
    const state = {
      items: [tab, { id: "new", kind: "new" } as const],
      active: "new",
    };
    const next = workspaceTabs(state, {
      type: "open",
      tab: { id: "unused", kind },
      replace,
    });
    expect(next.active).toBe(tab.id);
    expect(next.items).toBe(state.items);
    expect(next.items.filter((item) => item.kind === kind)).toHaveLength(1);
  },
);

it("closing an inactive tab leaves the current tab selected; closing the active one selects its neighbor", () => {
  const state = {
    items: [file, second, { id: "browser", kind: "browser" } as const],
    active: "second",
  };
  const inactive = workspaceTabs(state, {
    type: "close",
    id: "first",
    emptyId: "new",
  });
  expect(inactive.active).toBe("second");
  const active = workspaceTabs(state, {
    type: "close",
    id: "second",
    emptyId: "new",
  });
  expect(active.active).toBe("browser");
});

it("closing the last tab leaves a usable new tab", () => {
  expect(
    workspaceTabs(
      { items: [file], active: file.id },
      { type: "close", id: file.id, emptyId: "new" },
    ),
  ).toEqual(emptyTabs("new"));
});

it("retains a Changes selection and restores a Project's tab state", () => {
  const selection = {
    repository_path: "/",
    path: "first.txt",
    comparison: "staged" as const,
  };
  const content = {
    kind: "changes" as const,
    root: "/",
    selection,
    choices: [selection],
  };
  const state = workspaceTabs(emptyTabs("changes"), {
    type: "update",
    id: "changes",
    content,
  });
  expect(state.items[0]).toEqual({ ...content, id: "changes" });
  expect(workspaceTabs(emptyTabs("other"), { type: "restore", state })).toBe(
    state,
  );
});

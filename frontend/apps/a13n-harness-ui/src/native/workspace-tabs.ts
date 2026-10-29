import type { DiffSelection } from "./changes";

export type TabContent =
  | { kind: "new" }
  | { kind: "files"; directory: string; root: string }
  | { kind: "file"; path: string; root: string }
  | {
      kind: "changes";
      root: string;
      selection: DiffSelection | null;
      choices: DiffSelection[];
    }
  | { kind: "browser" }
  | { kind: "terminal" };

export type WorkspaceTab = TabContent & { id: string };
export type WorkspaceTabs = { items: WorkspaceTab[]; active: string };
export type TabAction =
  | { type: "open"; tab: WorkspaceTab; replace: boolean }
  | { type: "select"; id: string }
  | { type: "update"; id: string; content: TabContent }
  | { type: "navigate"; content: TabContent }
  | { type: "close"; id: string; emptyId: string }
  | { type: "restore"; state: WorkspaceTabs };

export function emptyTabs(id: string): WorkspaceTabs {
  return { items: [{ id, kind: "new" }], active: id };
}

function sameTarget(a: WorkspaceTab, b: WorkspaceTab) {
  if (a.kind !== b.kind) return false;
  switch (a.kind) {
    case "file":
      return b.kind === "file" && a.path === b.path;
    case "files":
      return b.kind === "files" && a.directory === b.directory;
    case "changes":
      return b.kind === "changes" && a.root === b.root;
    case "browser":
    case "terminal":
      return true;
    default:
      return false;
  }
}

export function workspaceTabs(
  state: WorkspaceTabs,
  action: TabAction,
): WorkspaceTabs {
  switch (action.type) {
    case "restore":
      return action.state;
    case "select":
      return state.items.some((tab) => tab.id === action.id)
        ? { ...state, active: action.id }
        : state;
    case "navigate":
    case "update": {
      const id = action.type === "navigate" ? state.active : action.id;
      return {
        ...state,
        items: state.items.map((tab) =>
          tab.id === id ? { ...action.content, id: tab.id } : tab,
        ),
      };
    }
    case "open": {
      const existing = state.items.find((tab) => sameTarget(tab, action.tab));
      if (
        existing &&
        (action.tab.kind === "browser" || action.tab.kind === "terminal")
      )
        return { ...state, active: existing.id };
      const id =
        existing?.id ?? (action.replace ? state.active : action.tab.id);
      const next = { ...action.tab, id };
      let items = state.items;
      if (existing) {
        items = items.map((tab) => (tab.id === id ? next : tab));
        if (action.replace && state.active !== id)
          items = items.filter((tab) => tab.id !== state.active);
      } else if (action.replace) {
        items = items.map((tab) => (tab.id === state.active ? next : tab));
      } else items = [...items, next];
      return { items, active: id };
    }
    case "close": {
      const index = state.items.findIndex((tab) => tab.id === action.id);
      if (index === -1) return state;
      const items = state.items.filter((tab) => tab.id !== action.id);
      if (!items.length) return emptyTabs(action.emptyId);
      return {
        items,
        active:
          state.active === action.id
            ? items[Math.min(index, items.length - 1)]!.id
            : state.active,
      };
    }
  }
}

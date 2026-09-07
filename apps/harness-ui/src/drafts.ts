import type { Model } from "./client";

// Tab-local draft state. Async acknowledgements may only consume their own revision.
export type Draft = {
  text: string;
  revision: number;
  pending: boolean;
  error: string;
  uncertain: boolean;
  receipt?: string;
  selection?: Pick<
    Model<"NewThreadDefaults">,
    "project_id" | "agent_id" | "environment_profile_id"
  >;
};
export type Drafts = Record<string, Draft>;
export const emptyDraft: Draft = {
  text: "",
  revision: 0,
  pending: false,
  error: "",
  uncertain: false,
};
export type Submission = { source: string; text: string; revision: number };
export type DraftAction =
  | { kind: "selection"; id: string; selection: Draft["selection"] }
  | { kind: "edit"; id: string; text: string }
  | { kind: "acknowledge"; id: string }
  | { kind: "begin"; submission: Submission }
  | { kind: "created"; submission: Submission; target: string }
  | {
      kind: "finish";
      submission: Submission;
      target: string;
      error?: string;
      uncertain?: boolean;
      receipt?: string;
    };
export function updateDrafts(state: Drafts, action: DraftAction): Drafts {
  if (action.kind === "selection")
    return {
      ...state,
      [action.id]: {
        ...(state[action.id] ?? emptyDraft),
        selection: action.selection,
      },
    };
  if (action.kind === "edit") {
    const draft = state[action.id] ?? emptyDraft;
    return {
      ...state,
      [action.id]: {
        ...draft,
        text: action.text,
        revision: draft.revision + 1,
      },
    };
  }
  if (action.kind === "acknowledge")
    return {
      ...state,
      [action.id]: {
        ...(state[action.id] ?? emptyDraft),
        uncertain: false,
        error: "",
      },
    };
  const { submission } = action;
  const source = state[submission.source] ?? emptyDraft;
  if (action.kind === "begin")
    return {
      ...state,
      [submission.source]: { ...source, pending: true, error: "" },
    };
  if (action.kind === "created")
    return {
      ...state,
      [submission.source]: {
        ...source,
        ...(source.revision === submission.revision
          ? { text: "", revision: source.revision + 1 }
          : {}),
      },
      [action.target]: { ...emptyDraft, text: submission.text, pending: true },
    };
  const target = state[action.target] ?? emptyDraft;
  const expectedRevision =
    action.target === submission.source ? submission.revision : 0;
  return {
    ...state,
    [submission.source]: { ...source, pending: false },
    [action.target]: {
      ...target,
      pending: false,
      error: action.error ?? "",
      uncertain: action.uncertain ?? false,
      receipt: action.receipt ?? target.receipt,
      ...(!action.error && target.revision === expectedRevision
        ? { text: "", revision: target.revision + 1 }
        : {}),
    },
  };
}

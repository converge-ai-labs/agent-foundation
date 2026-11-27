import {
  createContext,
  useContext,
  useCallback,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import { Button } from "a13n-ui";
import { GitBranch, PencilSimple } from "@phosphor-icons/react";
import { useTranslation } from "../i18n";
import { ErrorNotice } from "../shell/ui";
import { result, type Schema } from "../transport/client";
import { useTransport } from "../transport/context";
import { ComposerDrafts } from "./composer";
import { ThreadDraft } from "./draft";
import type { OrderedInputPart } from "./inline-attachments";
import { previewInput, type LocalInput } from "./local-input";
import {
  MessageEditEditor,
  type InlineEdit,
  type SubmitMessageEdit,
} from "./message-edit";
import { refreshThreadLists } from "./queries";
import {
  hasSkillTrigger,
  loadThreadSkills,
  skillReferences,
} from "./skill-references";

export type BranchActions = {
  active: boolean;
  pending: boolean;
  blocked: boolean;
  points?: Record<string, Schema<"BranchPoint">>;
  editSource?: string;
  create: (key: string) => Promise<void>;
  edit: SubmitMessageEdit;
  editor?: {
    draft: InlineEdit | null;
    setDraft: (draft: InlineEdit | null) => void;
    register: (anchor: string) => () => void;
  };
};
export const MessageBranches = createContext<BranchActions | undefined>(
  undefined,
);

export function MessageBranchProvider({
  thread,
  continuation,
  active,
  receipt,
  runId,
  draft,
  blocked,
  enabled,
  acpCommands,
  onEditPending,
  onRewound,
  children,
}: {
  thread: string;
  continuation?: string | null;
  active: boolean;
  receipt?: string | null;
  runId?: string | null;
  enabled: boolean;
  blocked: boolean;
  draft: ThreadDraft;
  acpCommands?: Schema<"AcpCommand">[];
  onEditPending: (pending: boolean) => void;
  onRewound: (value: Schema<"MessageEditReceipt">, input: LocalInput) => void;
  children: ReactNode;
}) {
  const transport = useTransport();
  const queries = useQueryClient();
  const navigate = useNavigate();
  const drafts = useContext(ComposerDrafts);
  const sending = useRef(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>();
  const [editDraft, setEditDraft] = useState<InlineEdit | null>(null);
  const [editAnchor, setEditAnchor] = useState<string | null>(null);
  const register = useCallback((anchor: string) => {
    setEditAnchor(anchor);
    return () =>
      setEditAnchor((current) => (current === anchor ? null : current));
  }, []);
  const points = useQuery({
    queryKey: ["thread", thread, "branch-points", continuation, receipt, runId],
    enabled: enabled && (!!continuation || !!receipt),
    staleTime: Infinity,
    queryFn: ({ signal }) =>
      result(
        transport.client.GET("/api/threads/{thread_id}/branch-points", {
          params: { path: { thread_id: thread } },
          signal,
        }),
      ),
  });
  async function create(key: string) {
    if (sending.current || !points.data) return;
    sending.current = true;
    setPending(true);
    setError(undefined);
    try {
      const copy = await result(
        transport.client.POST("/api/threads/{thread_id}/branch", {
          params: { path: { thread_id: thread } },
          body: {
            turn_key: key,
            expected_continuation_id: points.data.source,
            side: "after",
          },
        }),
      );
      const target = new ThreadDraft();
      target.modelOptions = draft.modelOptions;
      drafts.set(copy.thread_id, target);
      void refreshThreadLists(queries);
      navigate(`/threads/${copy.thread_id}`);
    } catch (failure) {
      setError(failure);
    } finally {
      sending.current = false;
      setPending(false);
    }
  }
  async function edit(
    selection: Schema<"MessageEditSelection">,
    parts: OrderedInputPart[],
    attachments: Map<string, Schema<"ThreadAttachment">>,
  ) {
    if (sending.current || blocked) return;
    sending.current = true;
    setPending(true);
    onEditPending(true);
    const id = `input_${crypto.randomUUID().replaceAll("-", "")}`;
    try {
      const excludedSlashNames = new Set(acpCommands?.map((item) => item.name));
      const catalog = hasSkillTrigger(parts, excludedSlashNames)
        ? await loadThreadSkills(transport, thread)
        : undefined;
      const replacement = await result(
        transport.client.POST("/api/threads/{thread_id}/edit-message", {
          params: { path: { thread_id: thread } },
          body: {
            selection,
            parts,
            source_id: id,
            skill_references: skillReferences(parts, catalog, {
              excludedSlashNames,
            }),
          },
        }),
      );
      onRewound(replacement, {
        id,
        action: "send",
        state: "accepted",
        parts: previewInput(
          id,
          parts,
          attachments,
          catalog,
          excludedSlashNames,
        ),
      });
    } finally {
      sending.current = false;
      setPending(false);
      onEditPending(false);
    }
  }
  return (
    <MessageBranches
      value={
        enabled
          ? {
              active,
              blocked,
              pending,
              points: points.data?.points,
              editSource: points.data?.edit_source,
              create,
              edit,
              editor: { draft: editDraft, setDraft: setEditDraft, register },
            }
          : undefined
      }
    >
      {children}
      {editDraft && !editAnchor && (
        <MessageEditEditor
          draft={editDraft}
          setDraft={setEditDraft}
          active={active}
          blocked
          pending={pending}
          submit={edit}
        />
      )}
      <ErrorNotice error={enabled && (error ?? points.error)} />
    </MessageBranches>
  );
}

export function MessageActionButton({
  kind,
  disabled,
  onClick,
}: {
  kind: "branch" | "edit";
  disabled: boolean;
  onClick: () => void;
}) {
  const { t } = useTranslation();
  const label = kind === "branch" ? t("Branch in new chat") : t("Edit message");
  if (disabled) return null;
  const Icon = kind === "branch" ? GitBranch : PencilSimple;
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon-sm"
      title={label}
      aria-label={label}
      onClick={onClick}
    >
      <Icon aria-hidden="true" />
    </Button>
  );
}

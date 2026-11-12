import {
  createContext,
  useContext,
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
import {
  ComposerDrafts,
  submitDraft,
  waitForSynchronization,
} from "./composer";
import { ThreadDraft } from "./draft";
import { refreshThreadLists } from "./queries";
import { loadThreadSkills } from "./skill-references";

export type BranchActions = {
  busy: boolean;
  pending: boolean;
  points?: Record<string, Schema<"BranchPoint">>;
  create: (key: string, edit?: string) => Promise<void>;
};
export const MessageBranches = createContext<BranchActions | undefined>(
  undefined,
);

export function MessageBranchProvider({
  thread,
  continuation,
  active,
  receipt,
  draft,
  enabled,
  children,
}: {
  thread: string;
  continuation?: string | null;
  active: boolean;
  receipt?: string | null;
  enabled: boolean;
  draft: ThreadDraft;
  children: ReactNode;
}) {
  const transport = useTransport();
  const queries = useQueryClient();
  const navigate = useNavigate();
  const drafts = useContext(ComposerDrafts);
  const sending = useRef(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>();
  const points = useQuery({
    queryKey: [
      "message-branch-points",
      thread,
      active,
      active ? receipt : continuation,
    ],
    enabled: enabled && (!!continuation || active),
    staleTime: Infinity,
    queryFn: () =>
      result(
        transport.client.GET("/api/threads/{thread_id}/branch-points", {
          params: {
            path: { thread_id: thread },
          },
        }),
      ),
  });
  async function create(key: string, edit?: string) {
    if (sending.current || (edit !== undefined && active) || !points.data)
      return;
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
            side: edit === undefined ? "after" : "before",
          },
        }),
      );
      const target = new ThreadDraft();
      target.modelOptions = draft.modelOptions;
      drafts.set(copy.thread_id, target);
      if (edit !== undefined) target.doc.getText("text").insert(0, edit);
      void refreshThreadLists(queries);
      navigate(`/threads/${copy.thread_id}`);
      if (edit !== undefined) {
        await submitDraft(
          target,
          transport,
          copy.thread_id,
          "send",
          undefined,
          undefined,
          undefined,
          () => loadThreadSkills(transport, copy.thread_id, target.environment),
          undefined,
          undefined,
          () => waitForSynchronization(target, new AbortController().signal),
        );
      }
    } catch (failure) {
      setError(failure);
    } finally {
      sending.current = false;
      setPending(false);
    }
  }
  return (
    <MessageBranches
      value={
        enabled
          ? {
              busy: active || pending,
              pending,
              points: points.data?.points,
              create,
            }
          : undefined
      }
    >
      {children}
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

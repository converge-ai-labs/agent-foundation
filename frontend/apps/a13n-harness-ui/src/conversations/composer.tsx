import { Dictation } from "./dictation";
import type { DictationTarget } from "./dictation-context";
import { useTranslation } from "../i18n";
import { composerMessage, stopMessage } from "./composer-messages";
import {
  cloneElement,
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { modelControlRequest } from "./model-controls";
import { useAppContextSelection } from "../mcp-apps/context-selection";
import { AppContextPendingError } from "../mcp-apps/app-session";
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame, Switch } from "a13n-ui";
import {
  Plus,
  ArrowUp,
  Stop,
  X,
  Target,
  CircleNotch,
} from "@phosphor-icons/react";
import type { EditorView } from "@codemirror/view";
import {
  attachmentSelections,
  attachmentToken,
  isReadyAttachment,
} from "./inline-attachments";
import { ImagePreview } from "../shell/image-preview";
import { TransportContext, useTransport } from "../transport/context";
import {
  ApiError,
  result,
  type Schema,
  type Transport,
} from "../transport/client";
import type { Profile } from "../shell/presence";
import { ConnectionNoticeContext } from "../shell/connection";
import { ConfirmAction } from "../shell/confirm-action";
import { ThreadDraft, values, type DraftCapture } from "./draft";
import { AnnotationChip } from "./annotation-chip";
import { withReplyAnnotations } from "./reply-quote";
import { ComposerEditor } from "./composer-editor";
import { CoordinatorIcon } from "./coordinator-icon";
import { useCoordinatorMutation } from "./coordinator-settings";
import { ClearContext } from "./clear-context";
import { TaskOptions } from "./task-options";
import { useStopOperation } from "./stop-operation";
import {
  loadThreadSkills,
  skillReferences,
  type LoadSkills,
} from "./skill-references";
import styles from "./conversation.module.css";
import { useTrackUnsent } from "./unsent";
import { previewInput, type LocalInput } from "./local-input";
import type { OrderedInputPart } from "./inline-attachments";

function beginInput(
  draft: ThreadDraft,
  action: "send" | "steer",
  attachments: Map<string, Schema<"ThreadAttachment">>,
  preset?: string,
  catalog?: Schema<"SkillCatalogView">,
): LocalInput {
  const text = preset ?? draft.doc.getText("text").toString();
  const parts: OrderedInputPart[] = [];
  let offset = 0;
  for (const selection of preset === undefined
    ? attachmentSelections(draft.doc)
    : []) {
    const start = selection.from;
    if (start > offset) parts.push(text.slice(offset, start));
    offset = selection.to;
    parts.push(
      isReadyAttachment(selection.id)
        ? { attachment_id: selection.id }
        : `[${draft.uploads.get(selection.key)?.file.name ?? "Attachment"}]`,
    );
  }
  if (offset < text.length) parts.push(text.slice(offset));
  const id = `input_${crypto.randomUUID().replaceAll("-", "")}`;
  const input: LocalInput = {
    id,
    action,
    parts: previewInput(
      id,
      withReplyAnnotations(
        parts,
        preset === undefined ? draft.annotations : [],
      ),
      attachments,
      catalog,
    ),
    state: "preparing",
  };
  draft.localInputs = [
    ...draft.localInputs.filter((item) => item.state !== "rejected"),
    input,
  ];
  draft.notify();
  return input;
}

export const ComposerDrafts = createContext(new Map<string, ThreadDraft>());
export function useDraft(threadId: string, historyOnly = false) {
  const drafts = useContext(ComposerDrafts);
  const shareId = useContext(TransportContext)?.shareId;
  let draft = drafts.get(threadId);
  if (!draft) {
    draft = new ThreadDraft();
    drafts.set(threadId, draft);
  }
  draft.bindAnnotations(threadId, shareId);
  useSyncExternalStore<unknown>(
    draft.subscribe,
    historyOnly ? draft.getHistorySnapshot : draft.getSnapshot,
  );
  useTrackUnsent(threadId, draft);
  return draft;
}

export function waitForSynchronization(
  draft: ThreadDraft,
  signal: AbortSignal,
) {
  signal.throwIfAborted();
  if (draft.synchronized) return Promise.resolve();
  return new Promise<void>((resolve, reject) => {
    const finish = (error?: Error) => {
      clearTimeout(timer);
      unsubscribe();
      signal.removeEventListener("abort", abort);
      if (error) reject(error);
      else resolve();
    };
    const abort = () =>
      finish(new Error("Cancelled. Your input is still saved here."));
    const unsubscribe = draft.subscribe(() => {
      if (draft.synchronized) finish();
      else if (draft.error || draft.replacement)
        finish(
          new Error(
            draft.error ||
              "The shared draft changed. Review your input before sending.",
          ),
        );
    });
    const timer = setTimeout(
      () =>
        finish(
          new Error(
            "Couldn't sync. Your input is saved here. Send again when connected.",
          ),
        ),
      15000,
    );
    signal.addEventListener("abort", abort, { once: true });
  });
}

export async function submitDraft(
  draft: ThreadDraft,
  transport: Transport,
  threadId: string,
  action: "send" | "steer",
  receipt?: string,
  localInput?: LocalInput,
  attachments = new Map<string, Schema<"ThreadAttachment">>(),
  loadSkills?: LoadSkills,
  signal?: AbortSignal,
  preset?: string,
  prepare?: () => Promise<void>,
  captureAppContext?: () => Schema<"AppContextReference">[],
) {
  if (
    draft.submission.kind === "pending" ||
    draft.submission.kind === "unknown"
  )
    return;
  const annotations = preset === undefined ? draft.annotations : [];
  // Own the shared submission state before any asynchronous preparation so
  // Retry and ordinary Send/Steer cannot race while synchronization or skills load.
  draft.submission = { kind: "pending", action };
  const input = localInput ?? beginInput(draft, action, attachments, preset);
  draft.notify();
  let captured: DraftCapture | undefined;
  let parts: OrderedInputPart[];
  let references: Schema<"SkillReference">[] = [];
  let skillCatalog: Schema<"SkillCatalogView"> | undefined;
  let appContext: Schema<"AppContextReference">[] = [];
  let capturing = false;
  try {
    if (action === "send") appContext = captureAppContext?.() ?? [];
    if (prepare) await prepare();
    signal?.throwIfAborted();
    capturing = true;
    captured = draft.capture();
    capturing = false;
    parts =
      preset === undefined
        ? captured.parts
        : [captured.parts.length ? `${preset}\n\n` : preset, ...captured.parts];
    if (
      !transport.shareId &&
      loadSkills &&
      parts.some(
        (part) => typeof part === "string" && /(?:^|\s)\$\S+/.test(part),
      )
    ) {
      skillCatalog = await loadSkills();
      references = skillReferences(parts, skillCatalog);
    }
    parts = withReplyAnnotations(parts, annotations);
    signal?.throwIfAborted();
  } catch (error) {
    input.state = "rejected";
    draft.submission = {
      kind: "rejected",
      message: error instanceof Error ? error.message : "Cannot capture input.",
      raw:
        error instanceof Error &&
        !capturing &&
        !(error instanceof AppContextPendingError),
    };
    captured?.doc.destroy();
    captured?.optionsDoc.destroy();
    draft.notify();
    return;
  }
  input.parts = previewInput(input.id, parts, attachments, skillCatalog);
  input.state = "pending";
  draft.submission = { kind: "pending", action };
  draft.notify();
  let localRejection = false;
  try {
    let acceptedReceipt: string;
    if (action === "send") {
      const { model, environment, mode } = captured.options;
      const { thinking, fast, reasoning_mode } = model ?? {};
      const controls = modelControlRequest({ thinking, fast, reasoning_mode });
      const selectedModel = model?.model_id;
      const accepted = await result(
        transport.client.POST("/api/threads/{thread_id}/submit", {
          params: { path: { thread_id: threadId } },
          body: {
            parts,
            draft: captured.snapshot,
            ...(!transport.shareId ? { mode } : {}),
            source_id: input.id,
            ...(references.length ? { skill_references: references } : {}),
            ...(appContext.length ? { app_context: appContext } : {}),
            ...(selectedModel ? { model_id: selectedModel } : {}),
            ...(environment ? { environment } : {}),
            ...controls,
          },
        }),
      );
      if (!accepted.receipt_id || accepted.thread_id !== threadId)
        throw new Error("Submission acknowledgement was incomplete.");
      acceptedReceipt = accepted.receipt_id;
    } else {
      if (!receipt) {
        localRejection = true;
        throw new ApiError(
          "The current operation is unavailable. Refresh its status.",
          409,
        );
      }
      const accepted = await result(
        transport.client.POST("/api/operations/{receipt_id}/steer", {
          params: { path: { receipt_id: receipt } },
          body: {
            parts,
            draft: captured.snapshot,
            source_id: input.id,
            ...(references.length ? { skill_references: references } : {}),
          },
        }),
      );
      if (accepted.receipt_id !== receipt)
        throw new Error(
          "Instruction acknowledgement did not match this operation.",
        );
      if (!accepted.accepted) {
        localRejection = true;
        throw new ApiError(
          "The run didn't accept your message. Your input is still saved here.",
          409,
        );
      }
      acceptedReceipt = receipt;
    }
    input.state = "accepted";
    if (captured) draft.clear(captured, action === "send");
    if (annotations.length)
      draft.setAnnotations(
        draft.annotations.filter((note) => !annotations.includes(note)),
      );
    draft.submission = {
      kind: "accepted",
      action,
      receipt: acceptedReceipt,
    };
  } catch (error) {
    // A definite application rejection differs from a lost response/proxy failure.
    if (
      error instanceof ApiError &&
      error.status >= 400 &&
      error.status < 500
    ) {
      input.state = "rejected";
      draft.submission = {
        kind: "rejected",
        message: error.message,
        raw: !localRejection,
      };
    } else {
      input.state = "unknown";
      draft.submission = {
        kind: "unknown",
        action,
        receipt,
        message:
          "Couldn't confirm delivery. Your message may already be sent and removed from the shared draft. Check the chat before sending again.",
      };
    }
  } finally {
    captured?.doc.destroy();
    captured?.optionsDoc.destroy();
    draft.notify();
  }
  return draft.submission.kind === "accepted";
}

export function submitContinuation(
  draft: ThreadDraft,
  transport: Transport,
  threadId: string,
  prepare?: () => Promise<void>,
  captureAppContext?: () => Schema<"AppContextReference">[],
) {
  return submitDraft(
    draft,
    transport,
    threadId,
    "send",
    undefined,
    undefined,
    undefined,
    () => loadThreadSkills(transport, threadId, draft.environment),
    undefined,
    "Continue completing the previous task.",
    async () => {
      if (prepare) await prepare();
      if (!draft.synchronized)
        await waitForSynchronization(draft, new AbortController().signal);
    },
    captureAppContext,
  );
}

export function Composer({
  referenceAdded = 0,
  autoFocus = false,
  local = false,
  skillDefaults,
  prepareThread,
  queuedInput,
  coordinator,
  owner,
  onPreparing,
  onSubmitted,
  onReviewOutcome,
  controls,
  leadingControls,
  threadId,
  activity,
  canRun,
  dictation,
  canClearContext = false,
  continuationId,
  unavailableReason,
  feedback,
  feedbackError,
  suppressConnectionNotice = false,
  profile,
  unauthorized,
  reconcile,
}: {
  threadId: string;
  activity: Schema<"RootActivityView">;
  canRun: boolean;
  dictation?: DictationTarget;
  canClearContext?: boolean;
  continuationId?: string | null;
  unavailableReason?: string;
  feedback?: ReactNode;
  feedbackError?: unknown;
  suppressConnectionNotice?: boolean;
  profile: Profile;
  unauthorized: () => void;
  reconcile: () => void;
  referenceAdded?: number;
  autoFocus?: boolean;
  local?: boolean;
  skillDefaults?: Schema<"NewThreadDefaults">;
  prepareThread?: () => Promise<void>;
  queuedInput?: { pending: boolean; locked: boolean; submit: () => void };
  coordinator?: { active: boolean; available: boolean; locked?: boolean };
  owner?: { title: string; onRemove?: () => void; locked?: boolean };
  onPreparing?: (preparing: boolean) => void;
  onSubmitted?: () => void | Promise<void>;
  onReviewOutcome?: () => void;
  controls?: () => ReactNode;
  leadingControls?: ReactNode;
}) {
  const { t } = useTranslation();
  const draft = useDraft(threadId);
  const [dictating, setDictating] = useState(false);
  const disconnected = useContext(ConnectionNoticeContext);
  const appContexts = useAppContextSelection();
  const promotion = useCoordinatorMutation({ thread_id: threadId });
  const [preparing, setPreparing] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [contextCleared, setContextCleared] = useState(false);
  const preparation = useRef<AbortController | null>(null);
  useEffect(() => () => preparation.current?.abort(), [threadId]);
  const transport = useTransport();
  const queries = useQueryClient();
  const coordinatorStatus = useMutation({
    mutationFn: () =>
      result(
        transport.client.GET("/api/threads/{thread_id}", {
          params: { path: { thread_id: threadId } },
        }),
      ),
    onSuccess: (detail) => {
      queries.setQueryData(["thread", threadId, "detail"], detail);
      draft.coordinatorUncertain = false;
      draft.notify();
    },
    retry: false,
  });
  const coordinatorActive =
    coordinator?.active ||
    promotion.data?.role === "coordinator" ||
    coordinatorStatus.data?.thread.role === "coordinator";
  const coordinatorBlocked =
    draft.coordinatorUncertain ||
    (!owner &&
      draft.coordinator &&
      !coordinatorActive &&
      !coordinator?.available);
  const connection = useRef<ReturnType<ThreadDraft["connect"]> | null>(null);
  const editorPresence = useRef<{
    draft: ThreadDraft;
    value: Schema<"DraftPresence">;
  } | null>(null);
  const upload = useRef<HTMLInputElement>(null);
  const editor = useRef<EditorView | null>(null);
  const sendButton = useRef<HTMLButtonElement>(null);
  const restoreEditorFocus = useRef(false);
  useEffect(() => {
    if (preparing || !restoreEditorFocus.current) return;
    restoreEditorFocus.current = false;
    // Inert preparation can blur the editor. Restore only our own focus, never
    // steal it from another field or a page the user opened while waiting.
    if (
      document.activeElement === document.body ||
      document.activeElement === sendButton.current
    )
      editor.current?.focus();
  }, [preparing]);
  useEffect(() => {
    if (!referenceAdded) return;
    const frame = requestAnimationFrame(() => {
      const view = editor.current;
      if (!view) return;
      view.dispatch({
        selection: { anchor: view.state.doc.length },
        scrollIntoView: true,
      });
      view.focus();
    });
    return () => cancelAnimationFrame(frame);
  }, [referenceAdded]);
  const previewRequest = useRef<AbortController | null>(null);
  useEffect(() => () => previewRequest.current?.abort(), [transport, threadId]);
  const [notice, setNotice] = useState<{
    message: string;
    values?: Record<string, string>;
    raw?: boolean;
  }>({ message: "" });
  const error = notice.message;
  const setError = (
    message: string,
    values?: Record<string, string>,
    raw = false,
  ) => setNotice({ message, values, raw });
  const skillContext = JSON.stringify([
    local,
    skillDefaults,
    draft.environment,
    activity.receipt_id,
  ]);
  const loadSkills: LoadSkills = () =>
    queries.fetchQuery({
      queryKey: ["thread", threadId, "skills", skillContext],
      queryFn: ({ signal }) =>
        local
          ? result(
              transport.client.POST("/api/threads/skills-preview", {
                body: {
                  ...skillDefaults,
                  ...draft.environment,
                },
                signal,
              }),
            )
          : loadThreadSkills(transport, threadId, draft.environment, signal),
      staleTime: 10000,
    });
  const [syncDelayed, setSyncDelayed] = useState(false);
  const synchronized = draft.synchronized;
  const showSyncStatus =
    !local &&
    !synchronized &&
    !draft.replacement &&
    !suppressConnectionNotice &&
    !disconnected &&
    !draft.error &&
    !error &&
    syncDelayed;
  useEffect(() => {
    setSyncDelayed(false);
    if (synchronized || local) return;
    const timer = setTimeout(() => setSyncDelayed(true), 700);
    return () => clearTimeout(timer);
  }, [synchronized, draft.status, local]);
  const [preview, setPreview] = useState<{
    name: string;
    text: string;
    notice?: string;
    url?: string;
    source?: Schema<"ThreadAttachment">["source"];
    image?: boolean;
  } | null>(null);
  const selections = attachmentSelections(draft.doc);
  const uploading = selections.some(
    ({ key, id }) =>
      id === "pending" && draft.uploads.get(key)?.status !== "staged",
  );
  const attachments = useQueries({
    queries: selections.map(({ id }) => ({
      enabled: (!local || !!queuedInput) && isReadyAttachment(id),
      queryKey: ["thread", threadId, "attachment", id],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        result(
          transport.client.GET(
            "/api/threads/{thread_id}/attachments/{attachment_id}/metadata",
            {
              params: { path: { thread_id: threadId, attachment_id: id! } },
              signal,
            },
          ),
        ),
      staleTime: Infinity,
    })),
  });
  useEffect(() => {
    if (local) return;
    const current = draft.connect(transport, threadId, unauthorized);
    connection.current = current;
    if (editorPresence.current?.draft === draft)
      current.presence(editorPresence.current.value);
    return () => {
      current.close();
      connection.current = null;
    };
  }, [draft, transport, threadId, unauthorized, local]);
  useEffect(
    () => () => {
      if (preview?.url) URL.revokeObjectURL(preview.url);
    },
    [preview],
  );
  const busy = activity.state !== "inactive";
  const pending = draft.submission.kind === "pending" || !!queuedInput?.pending;
  const unknown = draft.submission.kind === "unknown";
  const input = values(draft.doc);
  const missing = selections.some(({ key, id }, index) =>
    prepareThread && draft.uploads.get(key)?.status === "staged"
      ? false
      : !isReadyAttachment(id) || !attachments[index].data,
  );
  const hasInput = !!(
    input.prompt.trim() ||
    selections.length ||
    draft.annotations.length
  );
  const valid = hasInput && !missing;
  const cancellation = useStopOperation(threadId, draft, activity, reconcile);
  const { stopping, canStop } = cancellation;
  const stopNotice = cancellation.request
    ? stopMessage(cancellation.request, t)
    : undefined;
  const stop = () => {
    if (!canStop) return;
    // A shared draft can become empty during preparation. Abort unsubmitted
    // preparation without pretending to undo any request already admitted.
    preparation.current?.abort(
      new Error(
        "Stopped preparing the message. Your input is still saved here.",
      ),
    );
    return cancellation.stop();
  };
  const stopLabel = cancellation.retryable
    ? t("Retry Stop")
    : stopping
      ? t("Stopping")
      : t("Stop");
  const ready =
    local ||
    (draft.status === "Connected" && !draft.replacement && !draft.error);
  const canSteer =
    !queuedInput?.locked &&
    !cancellation.request &&
    busy &&
    ready &&
    !preparing &&
    !clearing &&
    !pending &&
    !unknown &&
    valid &&
    !!activity.available_actions?.includes("steer");
  const stopAction = !transport.shareId && busy && !hasInput;
  const canSend =
    canRun &&
    !queuedInput?.locked &&
    !coordinatorBlocked &&
    !busy &&
    ready &&
    !preparing &&
    !clearing &&
    !pending &&
    !unknown &&
    valid;
  const blockedReason =
    hasInput &&
    !preparing &&
    !pending &&
    !unknown &&
    !cancellation.request &&
    !draft.error &&
    !draft.replacement
      ? missing
        ? uploading
          ? t("Waiting for attachments…")
          : t("Resolve unavailable attachments before sending.")
        : coordinatorBlocked && !busy
          ? t("Choose a project and an idle chat to coordinate agents.")
          : !ready
            ? t("Waiting for the shared draft connection…")
            : !busy && !canRun
              ? unavailableReason
                ? composerMessage(unavailableReason, t)
                : t("Refresh chat status before sending.")
              : busy && !canSteer
                ? t("This operation cannot accept another message yet.")
                : undefined
      : undefined;
  const submit = async (action: "send" | "steer") => {
    if (dictating) return;
    if (action === "send" ? !canSend : !canSteer) return;
    if (queuedInput) {
      queuedInput.submit();
      return;
    }
    if (
      preparation.current ||
      draft.submission.kind === "pending" ||
      draft.submission.kind === "unknown"
    )
      return;
    const controller = new AbortController();
    preparation.current = controller;
    restoreEditorFocus.current =
      !!editor.current?.hasFocus ||
      document.activeElement === sendButton.current;
    const attachmentMetadata = () =>
      new Map(
        attachmentSelections(draft.doc).flatMap(({ id }) => {
          const item = queries.getQueryData<Schema<"ThreadAttachment">>([
            "thread",
            threadId,
            "attachment",
            id,
          ]);
          return item && id ? [[id, item] as const] : [];
        }),
      );
    const promote =
      action === "send" &&
      !owner &&
      draft.coordinator &&
      !coordinatorActive &&
      !prepareThread;
    const metadata = attachmentMetadata();
    const localInput = beginInput(
      draft,
      action,
      metadata,
      undefined,
      queries.getQueryData<Schema<"SkillCatalogView">>([
        "thread",
        threadId,
        "skills",
        skillContext,
      ]),
    );
    try {
      setPreparing(true);
      setContextCleared(false);
      onPreparing?.(true);
      setError("");
      const submitted = await submitDraft(
        draft,
        transport,
        threadId,
        action,
        activity.receipt_id ?? undefined,
        localInput,
        metadata,
        loadSkills,
        controller.signal,
        undefined,
        prepareThread || promote || !draft.synchronized
          ? async () => {
              if (prepareThread) {
                await prepareThread();
                controller.signal.throwIfAborted();
                await Promise.all(
                  attachmentSelections(draft.doc).flatMap(({ key }) => {
                    const item = draft.uploads.get(key);
                    return item?.status === "staged"
                      ? [uploadOne(key, item.file)]
                      : [];
                  }),
                );
              }
              // Explicit Send/Steer waits for the complete shared snapshot while
              // retaining ownership against other submission entry points.
              if (!draft.synchronized)
                await waitForSynchronization(draft, controller.signal);
              controller.signal.throwIfAborted();
              for (const [id, attachment] of attachmentMetadata())
                metadata.set(id, attachment);
              if (promote) {
                // The role write is separate from admission. A lost response is
                // not a rejection, and turning off local intent cannot undo it.
                draft.coordinatorUncertain = true;
                draft.notify();
                try {
                  await promotion.mutateAsync(undefined);
                  draft.coordinatorUncertain = false;
                  draft.notify();
                } catch (error) {
                  if (
                    error instanceof ApiError &&
                    error.status >= 400 &&
                    error.status < 500
                  ) {
                    draft.coordinatorUncertain = false;
                    draft.notify();
                  } else {
                    // Read only: recovery never repeats conversion or Send.
                    await coordinatorStatus.mutateAsync().catch(() => {});
                  }
                  throw error;
                }
              }
            }
          : undefined,
        () => appContexts?.capture(threadId) ?? [],
      );
      reconcile();
      if (submitted && !controller.signal.aborted) await onSubmitted?.();
    } catch (failure) {
      // A failed follow-up observation cannot undo an admission receipt.
      if (localInput.state === "preparing") {
        localInput.state = "rejected";
        draft.notify();
      }
      if (!controller.signal.aborted)
        setError(
          failure instanceof Error
            ? failure.message
            : "Couldn't prepare the chat. Your input is still saved here.",
          undefined,
          failure instanceof Error,
        );
    } finally {
      preparation.current = null;
      setPreparing(false);
      onPreparing?.(false);
    }
  };
  async function uploadOne(key: string, file: File) {
    setError("");
    const doc = draft.doc;
    const incarnation = draft.draftId;
    draft.uploads.set(key, { file, status: "pending" });
    doc.getMap("attachments").set(key, "pending");
    try {
      const response = await transport.fetch(
        `/api/threads/${encodeURIComponent(threadId)}/attachments?name=${encodeURIComponent(file.name)}`,
        {
          method: "POST",
          headers: { "Content-Type": file.type || "application/octet-stream" },
          body: file,
        },
      );
      const attachment = (await response.json()) as Schema<"ThreadAttachment">;
      queries.setQueryData(
        ["thread", threadId, "attachment", attachment.attachment_id],
        attachment,
      );
      if (
        draft.doc !== doc ||
        (incarnation !== undefined && draft.draftId !== incarnation) ||
        draft.replacement
      )
        return;
      // Keep the registry even if the token was deleted: native undo can restore it.
      doc.getMap("attachments").set(key, attachment.attachment_id);
      draft.uploads.delete(key);
    } catch (failure) {
      if (
        draft.doc !== doc ||
        (incarnation !== undefined && draft.draftId !== incarnation)
      )
        return;
      draft.uploads.set(key, { file, status: "failed" });
      doc.getMap("attachments").set(key, "failed");
      setError(
        failure instanceof Error
          ? failure.message
          : "Upload failed. Retry or remove the attachment.",
        undefined,
        failure instanceof Error,
      );
    }
  }
  async function uploadFiles(
    files: File[],
    from = editor.current?.state.selection.main.from ??
      draft.doc.getText("text").length,
    to = editor.current?.state.selection.main.to ?? from,
  ) {
    if (
      !files.length ||
      preparation.current ||
      queuedInput?.pending ||
      queuedInput?.locked
    )
      return;
    setError("");
    try {
      const attachments = attachmentSelections(draft.doc);
      // A pointer drop or a partial DOM selection can land inside a widget.
      for (const item of attachments) {
        if (item.from < from && from < item.to)
          from = from === to ? item.to : item.from;
        if (item.from < to && to < item.to) to = item.to;
      }
      const remaining = attachments.filter(
        (item) => item.to <= from || item.from >= to,
      );
      if (remaining.length + files.length > 8)
        throw new Error("Select up to eight attachments.");
      const total =
        remaining.reduce((size, { key, id }) => {
          const retained = queries.getQueryData<Schema<"ThreadAttachment">>([
            "thread",
            threadId,
            "attachment",
            id,
          ]);
          return (
            size + (retained?.size ?? draft.uploads.get(key)?.file.size ?? 0)
          );
        }, 0) + files.reduce((size, file) => size + file.size, 0);
      if (total > 20 * 1024 * 1024)
        throw new Error("Selected attachments exceed 20 MiB.");
      for (const file of files)
        if (file.size > 10 * 1024 * 1024) {
          setError("{{name}} exceeds 10 MiB.", { name: file.name });
          return;
        }
      // Replace the selection and reserve all positions as one undoable edit.
      let at = from;
      const pending = draft.doc.transact(() => {
        if (to > from) draft.doc.getText("text").delete(from, to - from);
        return files.map((file) => {
          const key = draft.addAttachment("pending", at);
          draft.uploads.set(key, {
            file,
            status: local && !queuedInput ? "staged" : "pending",
          });
          at += attachmentToken(key).length;
          return { key, file };
        });
      });
      draft.undo.stopCapturing();
      editor.current?.dispatch({
        selection: { anchor: at },
        scrollIntoView: true,
      });
      editor.current?.focus();
      if (!local || queuedInput)
        await Promise.all(pending.map(({ key, file }) => uploadOne(key, file)));
      else draft.notify();
    } catch (failure) {
      setError(
        failure instanceof Error ? failure.message : "Upload failed.",
        undefined,
        failure instanceof Error,
      );
    } finally {
      if (upload.current) upload.current.value = "";
    }
  }
  async function showAttachment(
    id: string,
    attachment?: Schema<"ThreadAttachment">,
  ) {
    setError("");
    previewRequest.current?.abort();
    const controller = new AbortController();
    previewRequest.current = controller;
    try {
      const response = await transport.fetch(
        `/api/threads/${encodeURIComponent(threadId)}/attachments/${encodeURIComponent(id)}`,
        { signal: controller.signal },
      );
      const blob = await response.blob();
      let text = "";
      let notice: string | undefined =
        "Binary attachment. Download to inspect its original bytes.";
      if (blob.size <= 256 * 1024) {
        try {
          const decoded = new TextDecoder("utf-8", { fatal: true }).decode(
            await blob.arrayBuffer(),
          );
          if (!decoded.includes("\0")) {
            text = decoded;
            notice = undefined;
          }
        } catch {
          /* Binary content is never injected into HTML. */
        }
      } else notice = "Too large to preview. Download the file to view it.";
      if (controller.signal.aborted) return;
      setPreview({
        name: attachment?.name ?? id,
        text,
        notice,
        url: URL.createObjectURL(blob),
        source: attachment?.source,
        image: attachment?.media_type.startsWith("image/"),
      });
    } catch (failure) {
      if (controller.signal.aborted) return;
      setError(
        failure instanceof Error ? failure.message : "Preview unavailable.",
        undefined,
        failure instanceof Error,
      );
    }
  }
  const goalToggle = (
    <Button
      variant={draft.mode === "goal" ? "secondary" : "ghost"}
      size="sm"
      className={styles.goalButton}
      aria-pressed={draft.mode === "goal"}
      title={t(
        "Keep working until the goal is done or the follow-up limit is reached.",
      )}
      disabled={!canRun || busy || preparing || pending || unknown}
      onClick={() => {
        draft.mode = draft.mode === "goal" ? "normal" : "goal";
        draft.notify();
      }}
    >
      <Target aria-hidden weight={draft.mode === "goal" ? "fill" : "regular"} />
      {t("Goal")}
    </Button>
  );
  const coordinatorDisabled =
    draft.coordinatorUncertain ||
    !!coordinator?.locked ||
    busy ||
    preparing ||
    pending ||
    unknown ||
    (!draft.coordinator && (!canRun || !coordinator?.available));
  const toggleCoordinator = () => {
    draft.coordinator = !draft.coordinator;
    draft.notify();
  };
  const ownerControl = owner && (
    <span
      className={styles.workerIdentity}
      title={t("Managed by {{name}}", { name: owner.title })}
    >
      <CoordinatorIcon size={16} />
      <span>{t("Managed by - {{name}}", { name: owner.title })}</span>
      {owner.onRemove && (
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t("Remove Coordinator assignment")}
          title={t("Create an independent chat instead")}
          disabled={owner.locked || preparing || pending || unknown}
          onClick={owner.onRemove}
        >
          <X aria-hidden />
        </Button>
      )}
    </span>
  );
  const coordinatorControl =
    !owner &&
    coordinator &&
    (coordinatorActive ? (
      <span
        className={styles.coordinatorIdentity}
        title={t("This chat is a coordinator. This can't be undone.")}
      >
        <CoordinatorIcon size={16} /> {t("Coordinator")}
      </span>
    ) : (
      <Button
        variant={draft.coordinator ? "secondary" : "ghost"}
        size="sm"
        className={styles.goalButton}
        aria-pressed={draft.coordinator}
        title={
          coordinator.available
            ? t(
                "Plan and delegate work to agents. Stays on after your first message.",
              )
            : t(
                "Choose a project, finish active work and answer pending requests first.",
              )
        }
        disabled={coordinatorDisabled}
        onClick={toggleCoordinator}
      >
        <CoordinatorIcon size={16} /> {t("Coordinate agents")}
      </Button>
    ));
  return (
    <section
      className={styles.composer}
      aria-label={busy ? t("Next message") : t("Message composer")}
    >
      <div className={styles.composerFeedback}>
        {feedback}
        {contextCleared && (
          <p role="status" className={styles.composerConnection}>
            {t(
              "Context cleared. Your next message starts fresh; chat history and your draft are kept.",
            )}
          </p>
        )}
        {draft.replacement && (
          <div role="alert" className={styles.warning}>
            <p>
              {t(
                "The shared draft changed. Keep your input or discard it to rejoin. Neither option sends a message.",
              )}
            </p>
            <Button
              variant="outline"
              onClick={() => draft.joinReplacement(true)}
            >
              {t("Keep my input")}
            </Button>
            <ConfirmAction
              key={draft.replacement.draft_id}
              trigger={
                <Button variant="ghost">{t("Discard and rejoin")}</Button>
              }
              title={t("Discard old input?")}
              description={t(
                "Discard this tab's old input and join the new shared draft?",
              )}
              confirmLabel={t("Discard and rejoin")}
              destructive
              onConfirm={() => draft.joinReplacement(false)}
            />
          </div>
        )}
        {draft.coordinatorUncertain && (
          <div className={styles.warning} role="alert">
            <p>
              {t(
                "Coordination may already be on. Check its status before sending. Your input is saved here.",
              )}
            </p>
            <Button
              variant="outline"
              loading={coordinatorStatus.isPending}
              onClick={() => coordinatorStatus.mutate()}
            >
              {t("Check status")}
            </Button>
          </div>
        )}
        {blockedReason &&
          (!feedback ||
            (!!queuedInput && canRun && activity.state !== "preparing")) &&
          !showSyncStatus &&
          !error &&
          draft.submission.kind !== "rejected" && (
            <p role="status" className={styles.composerConnection}>
              {blockedReason}
            </p>
          )}
        {cancellation.request && (
          <div className={styles.composerStatus}>
            <p
              role={
                cancellation.observationFailed ||
                ["uncertain", "rejected"].includes(cancellation.request.phase)
                  ? "alert"
                  : "status"
              }
            >
              {stopNotice}
              {cancellation.observationFailed && (
                <>
                  {" "}
                  {t(
                    "Current status is unavailable; stopping is not yet confirmed.",
                  )}
                </>
              )}
            </p>
            <Button variant="ghost" size="sm" onClick={cancellation.refresh}>
              {t("Check stop status")}
            </Button>
          </div>
        )}
        {(error ||
          (draft.error &&
            draft.error !==
              (feedbackError instanceof Error
                ? feedbackError.message
                : feedbackError))) &&
          draft.submission.kind !== "rejected" &&
          !unknown && (
            <p role="alert" className={styles.warning}>
              {(error ? notice.raw : draft.errorRaw)
                ? error || draft.error
                : composerMessage(
                    error || draft.error,
                    t,
                    error ? notice.values : undefined,
                  )}
            </p>
          )}
        {(draft.submission.kind === "rejected" || unknown) && (
          <div role="alert" className={styles.warning}>
            <p>
              {"message" in draft.submission &&
                (draft.submission.kind === "rejected" && draft.submission.raw
                  ? draft.submission.message
                  : composerMessage(draft.submission.message, t))}
            </p>
            {unknown &&
              "receipt" in draft.submission &&
              draft.submission.receipt && (
                <details>
                  <summary>{t("Message details")}</summary>
                  <small>
                    {t("Operation: {{receipt}}", {
                      receipt: draft.submission.receipt,
                    })}
                  </small>
                </details>
              )}
            {unknown && (
              <>
                <Button
                  variant="outline"
                  onClick={onReviewOutcome ?? reconcile}
                >
                  {onReviewOutcome ? t("Review message") : t("Refresh chat")}
                </Button>
                {!onReviewOutcome && (
                  <ConfirmAction
                    trigger={
                      <Button variant="ghost">{t("Allow new message")}</Button>
                    }
                    title={t("Enable a new submission?")}
                    description={t(
                      "Your message may already be sent. Check the chat before allowing another message.",
                    )}
                    confirmLabel={t("Enable submission")}
                    onConfirm={() => {
                      draft.submission = { kind: "idle" };
                      draft.notify();
                    }}
                  />
                )}
              </>
            )}
          </div>
        )}
      </div>
      <div className={styles.composerBody}>
        {(draft.mode === "goal" ||
          ownerControl ||
          coordinatorActive ||
          draft.coordinator) && (
          <div className={styles.composerHeader}>
            {!transport.shareId &&
              draft.mode === "goal" &&
              cloneElement(goalToggle, {
                "aria-label": t("Turn off keep working"),
              })}
            {ownerControl ||
              (coordinatorActive
                ? coordinatorControl
                : draft.coordinator && coordinatorControl
                  ? cloneElement(coordinatorControl, {
                      "aria-label": t("Turn off agent coordination"),
                    })
                  : null)}
          </div>
        )}
        <div
          className={styles.composerInput}
          data-dictating={dictating || undefined}
        >
          <input
            ref={upload}
            type="file"
            multiple
            hidden
            onChange={(event) =>
              void uploadFiles(Array.from(event.target.files ?? []))
            }
          />
          <Button
            variant="ghost"
            size="icon-sm"
            className={styles.attachButton}
            aria-label={t("Attach files")}
            title={t("Attach files - you can also paste or drop files")}
            loading={uploading}
            disabled={preparing || pending || queuedInput?.locked}
            onClick={() => upload.current?.click()}
          >
            <Plus />
          </Button>
          <div
            inert={preparing || pending || queuedInput?.locked}
            className={styles.composerEditor}
          >
            <AnnotationChip />
            <ComposerEditor
              autoFocus={autoFocus}
              local={local}
              skillContext={skillContext}
              loadSkills={
                transport.shareId || queuedInput
                  ? undefined
                  : async () => {
                      try {
                        return await loadSkills();
                      } catch (failure) {
                        setError(
                          failure instanceof Error
                            ? "Could not load skills: {{message}}"
                            : "Could not load skills. Type $ again to retry.",
                          failure instanceof Error
                            ? { message: failure.message }
                            : undefined,
                        );
                        throw failure;
                      }
                    }
              }
              draft={draft}
              profile={profile}
              presence={(value) => {
                editorPresence.current = { draft, value };
                connection.current?.presence(value);
              }}
              submit={() => void submit(busy ? "steer" : "send")}
              editor={editor}
              attachments={{
                transport,
                threadId,
                metadata: new Map(
                  attachments.flatMap((item) =>
                    item.data
                      ? [[item.data.attachment_id, item.data] as const]
                      : [],
                  ),
                ),
                preview: (id, attachment) =>
                  void showAttachment(id, attachment),
                previewLocal: (file) =>
                  setPreview({
                    name: file.name,
                    image: true,
                    text: "",
                    url: URL.createObjectURL(file),
                  }),
                upload: uploadFiles,
                retry: (key) => {
                  const item = draft.uploads.get(key);
                  if (
                    (!local || queuedInput) &&
                    (item?.status === "failed" || item?.status === "staged")
                  )
                    void uploadOne(key, item.file);
                  else if (!item)
                    setError(
                      "This upload is unavailable in this tab. Remove it and attach the file again.",
                    );
                },
              }}
            />
          </div>
          <div className={styles.composerActions}>
            <span className={styles.composerSync}>
              {showSyncStatus && draft.status === "Connected" && (
                <span
                  role="status"
                  aria-label={t("Syncing edits…")}
                  title={t("Syncing edits…")}
                >
                  <CircleNotch
                    className={styles.threadRunning}
                    aria-hidden="true"
                  />
                </span>
              )}
            </span>
            {!transport.shareId && !queuedInput && (
              <TaskOptions
                coordinatorEnabled={draft.coordinator}
                goal={
                  <Switch
                    aria-label={t("Goal")}
                    title={goalToggle.props.title}
                    checked={draft.mode === "goal"}
                    disabled={
                      !canRun || busy || preparing || pending || unknown
                    }
                    onCheckedChange={(enabled) => {
                      draft.mode = enabled ? "goal" : "normal";
                      draft.notify();
                    }}
                  />
                }
                coordinator={
                  !owner && !coordinatorActive && coordinator ? (
                    <Switch
                      aria-label={t("Coordinate agents")}
                      title={
                        coordinatorControl
                          ? coordinatorControl.props.title
                          : undefined
                      }
                      checked={draft.coordinator}
                      disabled={coordinatorDisabled}
                      onCheckedChange={(enabled) => {
                        draft.coordinator = enabled;
                        draft.notify();
                      }}
                    />
                  ) : undefined
                }
                environment={leadingControls}
                context={
                  !local && (
                    <ClearContext
                      threadId={threadId}
                      continuationId={continuationId}
                      disabled={
                        !canClearContext ||
                        busy ||
                        preparing ||
                        pending ||
                        unknown
                      }
                      onPendingChange={setClearing}
                      onCleared={() => setContextCleared(true)}
                      reconcile={reconcile}
                    />
                  )
                }
              />
            )}
            <div className={styles.composerChoices}>{controls?.()}</div>
            {dictation && !transport.shareId && (
              <Dictation
                key={threadId}
                target={dictation}
                disabled={preparing || pending || queuedInput?.locked}
                onActive={setDictating}
                readDraft={() => {
                  const view = editor.current;
                  if (!view) return;
                  const { doc, selection } = view.state;
                  const { from, to } = selection.main;
                  return {
                    before: doc.sliceString(0, from),
                    selected: doc.sliceString(from, to),
                    after: doc.sliceString(to),
                  };
                }}
                insert={(text) => {
                  const view = editor.current;
                  if (!view) return;
                  view.dispatch(view.state.replaceSelection(text));
                  requestAnimationFrame(() => view.focus());
                }}
              />
            )}
          </div>
          <Button
            ref={sendButton}
            size="icon"
            className={styles.sendButton}
            data-action={stopAction ? "stop" : "send"}
            aria-label={
              stopAction
                ? stopLabel
                : pending
                  ? t("Submitting")
                  : preparing
                    ? t("Preparing")
                    : busy
                      ? t("Steer")
                      : t("Send")
            }
            title={
              (stopAction ? stopNotice : undefined) ??
              blockedReason ??
              (stopAction
                ? t("Stop this operation")
                : busy
                  ? t("Steer current operation - Enter")
                  : t("Send message - Enter"))
            }
            disabled={stopAction ? !canStop : busy ? !canSteer : !canSend}
            loading={stopAction ? stopping : pending || preparing}
            onClick={() =>
              stopAction ? void stop() : void submit(busy ? "steer" : "send")
            }
          >
            <span className={styles.sendGlyph} aria-hidden="true">
              <ArrowUp />
              <Stop weight="fill" />
            </span>
          </Button>
        </div>
      </div>
      {preview?.image && preview.url && (
        <ImagePreview
          src={preview.url}
          name={preview.name}
          close={() => setPreview(null)}
        />
      )}
      <ModalFrame
        bottomStickOnMobile
        open={!!preview && !preview.image}
        onOpenChange={(open) => {
          if (!open) {
            previewRequest.current?.abort();
            setPreview(null);
          }
        }}
        title={preview?.name ?? t("Attachment")}
        description={t(
          "This is the attached snapshot. The original file may have changed.",
        )}
        closeLabel={t("Close")}
      >
        {preview?.source && (
          <div className={styles.summary}>
            {"path" in preview.source && (
              <p>
                {t("Server snapshot - {{path}}", { path: preview.source.path })}
              </p>
            )}
            {"repository_path" in preview.source && (
              <p>
                {preview.source.comparison} - {preview.source.repository_path} -
                HEAD {preview.source.head_oid ?? t("empty tree")}
              </p>
            )}
            <details>
              <summary>{t("Source")}</summary>
              <pre className={styles.code}>
                {JSON.stringify(preview.source, null, 2)}
              </pre>
            </details>
            <p>
              {t(
                "This is a saved snapshot. Sending keeps it and its source details in the chat, even if the original changes.",
              )}
            </p>
          </div>
        )}
        <pre className={styles.code}>
          {preview?.notice ? composerMessage(preview.notice, t) : preview?.text}
        </pre>
        {preview?.url && (
          <a href={preview.url} download={preview.name}>
            {t("Download original")}
          </a>
        )}
      </ModalFrame>
    </section>
  );
}

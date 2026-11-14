import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, NavLink } from "react-router";
import {
  Button,
  Menu,
  MenuTrigger,
  MenuPopup,
  MenuItem,
  useMediaQuery,
} from "a13n-ui";
import { Robot, Pause, Play, Gear, DotsThree } from "@phosphor-icons/react";
import { useTranslation } from "./i18n";
import { ApiError, result, type Schema } from "./transport/client";
import { useTransport } from "./transport/context";
import { ErrorNotice } from "./shell/ui";
import type { Profile } from "./shell/presence";
import { Conversation } from "./conversations/conversation";
import {
  Composer,
  useDraft,
  waitForSynchronization,
} from "./conversations/composer";
import { RunActivity } from "./conversations/run-activity";
import type { PublishedMessage } from "./conversations/transcript";
import { botConversationError } from "./bot-conversation";
import { useDebug } from "./shell/debug";
import { useResults } from "./conversations/results";
import row from "./shell/navigation-row.module.css";
import styles from "./bot.module.css";

import { BotSetup, BotSetupProvider } from "./bot-setup";
import { BOT_THREAD_ID } from "./bot-identity";
export { BOT_THREAD_ID } from "./bot-identity";

export function useBot(enabled = true) {
  const { client, shareId } = useTransport();
  return useQuery({
    queryKey: ["bot"],
    enabled: enabled && !shareId,
    queryFn: ({ signal }) => result(client.GET("/api/bot", { signal })),
  });
}

export function BotNavigation() {
  const { t } = useTranslation();
  const results = useResults();
  const unread = results.tracker?.isUnread(BOT_THREAD_ID);
  return (
    <NavLink to="/bot" className={`${row.row} ${styles.navigation}`}>
      <Robot aria-hidden="true" />
      <span>{t("Bot")}</span>
      {unread && (
        <span
          className={styles.unread}
          aria-label={t("New result")}
          title={t("New result")}
        />
      )}
    </NavLink>
  );
}

type BotSubmission = {
  command: Schema<"BotSubmitRequest">;
  message: Schema<"BotMessage">;
  phase: "pending" | "unknown" | "rejected";
};

function useBotComposer() {
  const { client } = useTransport();
  const queries = useQueryClient();
  const results = useResults();
  const { data } = useBot();
  const draft = useDraft(BOT_THREAD_ID);
  const unacknowledgedEdits = draft.hasUnacknowledgedEdits;
  const preparation = useRef<AbortController | null>(null);
  const [preparing, setPreparing] = useState(false);
  const [preparationError, setPreparationError] = useState<unknown>();
  useEffect(() => () => preparation.current?.abort(), []);
  // The ref is also a synchronous duplicate-click latch. Recovery retains the
  // original command across navigation; observation never replays it.
  const current = useRef<BotSubmission | null>(null);
  const [submission, setSubmission] = useState<BotSubmission | null>(null);
  const update = (value: BotSubmission | null) => {
    current.current = value;
    setSubmission(value);
  };
  const refresh = () => queries.invalidateQueries({ queryKey: ["bot"] });
  const send = useMutation({
    retry: false,
    mutationFn: (value: BotSubmission) =>
      result(client.POST("/api/bot/inputs", { body: value.command })),
    onSuccess: (accepted, value) => {
      // Admission owns the canonical row without waiting for a heavy snapshot.
      queries.setQueryData<Schema<"BotSnapshot">>(["bot"], (snapshot) => {
        if (!snapshot) return snapshot;
        const message: Schema<"BotMessage"> = {
          message_id: `user-${accepted.input_id}`,
          role: "user",
          text: accepted.text,
          parts: accepted.parts,
          input_id: accepted.input_id,
          created_at: accepted.created_at,
        };
        return {
          ...snapshot,
          messages: [
            ...(snapshot.messages ?? []).filter(
              (item) => item.message_id !== message.message_id,
            ),
            message,
          ],
          inputs: [
            ...(snapshot.inputs ?? []).filter(
              (item) => item.input_id !== accepted.input_id,
            ),
            accepted,
          ],
        };
      });
      if (current.current?.command.input_id === value.command.input_id)
        update(null);
      void refresh();
      results.tracker?.invalidate(BOT_THREAD_ID);
    },
    onError: (error, value) => {
      if (current.current?.command.input_id !== value.command.input_id) return;
      const rejected =
        error instanceof ApiError && error.status >= 400 && error.status < 500;
      update({ ...value, phase: rejected ? "rejected" : "unknown" });
      void refresh();
    },
  });
  useEffect(() => {
    if (submission?.phase !== "unknown") return;
    const id = submission.command.input_id;
    if (
      // A retained input can be interrupted before user publication. Keep its
      // local row and exact-retry recovery until a canonical message exists.
      data?.messages?.some((message) => message.message_id === `user-${id}`)
    ) {
      current.current = null;
      setSubmission(null);
      send.reset();
    }
  }, [data, submission, send.reset]);
  useEffect(() => {
    if (
      !unacknowledgedEdits &&
      submission?.phase !== "pending" &&
      submission?.phase !== "unknown"
    )
      return;
    const protect = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [unacknowledgedEdits, submission?.phase]);
  const submit = async () => {
    if (current.current || preparation.current) return;
    const controller = new AbortController();
    preparation.current = controller;
    setPreparing(true);
    setPreparationError(undefined);
    try {
      await waitForSynchronization(draft, controller.signal);
      const captured = draft.capture();
      const { parts, snapshot } = captured;
      captured.doc.destroy();
      captured.optionsDoc.destroy();
      const command: Schema<"BotSubmitRequest"> = {
        input_id: crypto.randomUUID(),
        text: parts.filter((part) => typeof part === "string").join(""),
        parts,
        draft: snapshot,
      };
      const value: BotSubmission = {
        command,
        phase: "pending",
        message: {
          message_id: `user-${command.input_id}`,
          role: "user",
          text: command.text ?? "",
          created_at: new Date().toISOString(),
          parts: parts.map((part) =>
            typeof part === "string"
              ? part
              : queries.getQueryData<Schema<"ThreadAttachment">>([
                  "thread",
                  BOT_THREAD_ID,
                  "attachment",
                  part.attachment_id,
                ])!,
          ),
        },
      };
      update(value);
      // Admission consumes captured CRDT identities on the server. Peer edits
      // and rejected input remain in the ordinary shared draft.
      send.mutate(value);
    } catch (error) {
      setPreparationError(error);
    } finally {
      preparation.current = null;
      setPreparing(false);
    }
  };
  const retry = () => {
    if (current.current?.phase !== "unknown") return;
    const value = { ...current.current, phase: "pending" as const };
    update(value);
    send.mutate(value);
  };
  const discard = () => {
    if (current.current?.phase !== "rejected") return;
    update(null);
    send.reset();
  };
  return {
    send,
    submission,
    submit,
    retry,
    discard,
    edit: discard,
    preparing,
    preparationError,
  };
}

const BotComposerContext = createContext<ReturnType<
  typeof useBotComposer
> | null>(null);
// Keep the exact uncertain command alive across navigation. It is never replayed automatically.
export function BotComposerProvider({ children }: { children: ReactNode }) {
  const composer = useBotComposer();
  return (
    <BotSetupProvider>
      <BotComposerContext value={composer}>{children}</BotComposerContext>
    </BotSetupProvider>
  );
}

export function BotActions({
  children,
  trailingActions,
}: {
  children?: ReactNode;
  trailingActions?: ReactNode;
}) {
  const { t } = useTranslation();
  const compact = useMediaQuery("(max-width: 760px)");
  const { client } = useTransport();
  const queries = useQueryClient();
  const { data } = useBot();
  const pause = useMutation({
    retry: false,
    mutationFn: (paused: boolean) =>
      result(client.POST("/api/bot/pause", { body: { paused } })),
    onSuccess: (snapshot) => queries.setQueryData(["bot"], snapshot),
  });
  if (compact)
    return (
      <>
        <Menu>
          <MenuTrigger
            render={<Button variant="ghost" size="icon-sm" />}
            aria-label={t("Chat actions")}
            title={t("Chat actions")}
          >
            <DotsThree />
          </MenuTrigger>
          <MenuPopup align="end">
            {children}
            <MenuItem render={<Link to="/settings#bot" />}>
              <Gear />
              {t("Bot settings")}
            </MenuItem>
            {data?.enabled && (
              <MenuItem
                disabled={!data.available || pause.isPending}
                onClick={() => pause.mutate(!data.state.paused)}
              >
                {data.state.paused ? <Play /> : <Pause />}
                {data.state.paused
                  ? t("Resume automation")
                  : t("Pause automation")}
              </MenuItem>
            )}
            {trailingActions}
          </MenuPopup>
        </Menu>
        <ErrorNotice error={pause.error} />
      </>
    );
  if (data && !data.enabled && !data.thread && !data.messages?.length)
    return null;
  return (
    <>
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={t("Bot settings")}
        title={t("Bot settings")}
        render={<Link to="/settings#bot" />}
      >
        <Gear />
      </Button>
      {data?.state.paused && (
        <span
          className={styles.status}
          title={t("Automation paused · You can still chat")}
        >
          {t("Automation paused")}
        </span>
      )}
      {data?.enabled && (
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={
            data.state.paused ? t("Resume automation") : t("Pause automation")
          }
          title={
            data.state.paused ? t("Resume automation") : t("Pause automation")
          }
          disabled={!data.available || pause.isPending}
          onClick={() => pause.mutate(!data.state.paused)}
        >
          {data.state.paused ? <Play /> : <Pause />}
        </Button>
      )}
      <ErrorNotice error={pause.error} />
    </>
  );
}

export function BotPage({
  profile,
  unauthorized,
}: {
  profile: Profile;
  unauthorized: () => void;
}) {
  const { t } = useTranslation();
  const bot = useBot();
  const data = bot.data;
  const debug = useDebug();
  const messages = data?.messages ?? [];
  const composer = useContext(BotComposerContext);
  if (!composer) throw new Error("Bot composer is missing.");
  const {
    send,
    submission,
    submit,
    retry,
    discard,
    edit,
    preparing,
    preparationError,
  } = composer;
  const visibleMessages: PublishedMessage[] = messages.map((message) => ({
    ...message,
    status: data?.inputs?.some(
      (input) =>
        input.input_id &&
        `user-${input.input_id}` === message.message_id &&
        input.state === "interrupted",
    )
      ? t("Didn't finish")
      : undefined,
  }));
  if (
    submission &&
    !messages.some(
      (message) => message.message_id === submission.message.message_id,
    )
  )
    visibleMessages.push({
      ...submission.message,
      local: true,
      status:
        submission.phase === "pending"
          ? t("Sending…")
          : submission.phase === "unknown"
            ? t("Not confirmed")
            : t("Not sent"),
    });
  const operation = data?.operation;
  const activity: Schema<"RootActivityView"> = operation
    ? {
        state: operation.status === "preparing" ? "preparing" : "running",
        receipt_id: operation.receipt.receipt_id,
        available_actions: data?.thread?.root_activity?.available_actions ?? [
          "cancel",
          ...(operation.status === "running" ? ["steer" as const] : []),
        ],
      }
    : { state: "inactive" };
  if (!data?.enabled || !data.available || data.pending_decision)
    activity.available_actions = [];
  const busy =
    !!data?.operation ||
    data?.inputs?.some((input) =>
      ["queued", "dispatching", "admitted"].includes(input.state ?? "queued"),
    );
  if (data && !data.enabled && !data.thread && !data.messages?.length)
    return <BotSetup />;
  const status =
    data && !data.enabled
      ? t("Bot is off")
      : !data?.available && data
        ? t("Bot processing is unavailable")
        : data?.pending_decision
          ? t("Bot needs your decision")
          : operation?.status === "preparing"
            ? t("Bot is starting. Your draft is kept; send when it is working.")
            : null;
  return (
    <Conversation
      threadId={BOT_THREAD_ID}
      profile={profile}
      unauthorized={unauthorized}
      publication={{
        status: null,
        threadAvailable: !!data?.thread,
        ready: !!data || bot.isError,
        revision: JSON.stringify([visibleMessages, busy, send.isPending]),
        completionVersion: data?.thread?.completion?.version ?? 0,
        messages: visibleMessages,
        activity: (
          <RunActivity
            submission={
              send.isPending
                ? {
                    kind: "pending",
                    action: activity.state === "running" ? "steer" : "send",
                  }
                : { kind: "idle" }
            }
            activity={
              busy && !data?.pending_decision
                ? operation
                  ? activity
                  : { state: "preparing" }
                : undefined
            }
            operation={
              data?.pending_decision
                ? undefined
                : (data?.operation ?? undefined)
            }
          />
        ),
        composer: (dictation) =>
          data && !data.enabled ? (
            <div className={styles.offBar}>
              <div>
                <strong>{t("Bot is off")}</strong>
                <p>
                  {t(
                    "History and pending inputs are kept. Turn Bot on in Settings to continue.",
                  )}
                </p>
              </div>
              <Button variant="outline" render={<Link to="/settings#bot" />}>
                {t("Bot settings")}
              </Button>
            </div>
          ) : (
            <Composer
              dictation={dictation}
              threadId={BOT_THREAD_ID}
              activity={activity}
              canRun={
                !!data?.enabled && !!data.available && !data.pending_decision
              }
              queuedInput={{
                pending: send.isPending || preparing,
                locked: !!submission,
                submit,
              }}
              profile={profile}
              unauthorized={unauthorized}
              reconcile={() => void bot.refetch()}
              controls={() => null}
              feedback={
                <>
                  <ErrorNotice
                    error={bot.error}
                    retry={() => void bot.refetch()}
                  />
                  <ErrorNotice error={preparationError} />
                  {status && <p role="status">{status}</p>}
                  {submission && submission.phase !== "pending" && (
                    <div role="alert" className={styles.recovery}>
                      <p>
                        {submission.phase === "unknown"
                          ? t(
                              "Delivery is not confirmed. Check status or retry the exact same message; it will not be sent twice.",
                            )
                          : t(
                              "This message was not sent. Edit it or discard it before continuing.",
                            )}
                      </p>
                      <ErrorNotice error={send.error} />
                      <div className={styles.recoveryActions}>
                        {submission.phase === "unknown" ? (
                          <>
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => void bot.refetch()}
                            >
                              {t("Check status")}
                            </Button>
                            <Button variant="outline" size="sm" onClick={retry}>
                              {t("Retry exactly")}
                            </Button>
                          </>
                        ) : (
                          <>
                            <Button variant="outline" size="sm" onClick={edit}>
                              {t("Edit message")}
                            </Button>
                            <Button variant="ghost" size="sm" onClick={discard}>
                              {t("Discard")}
                            </Button>
                          </>
                        )}
                      </div>
                    </div>
                  )}
                  <ErrorNotice
                    error={botConversationError(
                      data?.state.last_error,
                      debug.enabled,
                    )}
                  />
                  {data && !data.available && (
                    <p>
                      {t(
                        "Check Bot's model in Settings. Another app may also own Bot processing.",
                      )}{" "}
                      <Link to="/settings#bot">{t("Bot settings")}</Link>
                    </p>
                  )}
                </>
              }
            />
          ),
      }}
    />
  );
}

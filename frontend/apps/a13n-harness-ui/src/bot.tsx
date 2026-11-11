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
import { Button } from "a13n-ui";
import { Robot, ListBullets, Pause, Play, Gear } from "@phosphor-icons/react";
import { useTranslation } from "./i18n";
import { ApiError, result, type Schema } from "./transport/client";
import { useTransport } from "./transport/context";
import { ErrorNotice } from "./shell/ui";
import type { Profile } from "./shell/presence";
import { Conversation } from "./conversations/conversation";
import { Composer, useDraft } from "./conversations/composer";
import { PublishedTranscript } from "./conversations/transcript";
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

function useBotComposer() {
  const { client } = useTransport();
  const queries = useQueryClient();
  const results = useResults();
  const draft = useDraft(BOT_THREAD_ID);
  const text = draft.doc.getText("text").toString();
  const command = useRef<Schema<"BotSubmit"> | null>(null);
  const [uncertain, setUncertain] = useState(false);
  const refresh = () => queries.invalidateQueries({ queryKey: ["bot"] });
  const send = useMutation({
    retry: false,
    mutationFn: async () => {
      command.current ??= { input_id: crypto.randomUUID(), text };
      const data = queries.getQueryData<Schema<"BotSnapshot">>(["bot"]);
      if (data?.thread) await results.tracker?.follow(data.thread);
      else await results.tracker?.followVersion(BOT_THREAD_ID, 0);
      return result(client.POST("/api/bot/inputs", { body: command.current }));
    },
    onSuccess: () => {
      command.current = null;
      setUncertain(false);
      const input = draft.doc.getText("text");
      input.delete(0, input.length);
      draft.notify();
      void refresh();
      results.tracker?.invalidate(BOT_THREAD_ID);
    },
    onError: (error) => {
      const rejected = error instanceof ApiError && error.status < 500;
      setUncertain(!rejected);
      if (rejected) command.current = null;
      void refresh();
    },
  });
  useEffect(() => {
    if (!text && !command.current) return;
    const protect = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [text, uncertain]);
  return { send, uncertain };
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

export function BotActions() {
  const { t } = useTranslation();
  const { client } = useTransport();
  const queries = useQueryClient();
  const { data } = useBot();
  const pause = useMutation({
    retry: false,
    mutationFn: (paused: boolean) =>
      result(client.POST("/api/bot/pause", { body: { paused } })),
    onSuccess: (snapshot) => queries.setQueryData(["bot"], snapshot),
  });
  if (data && !data.enabled && !data.thread && !data.messages?.length)
    return null;
  return (
    <>
      {data?.thread && (
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t("Activity")}
          title={t("Activity")}
          render={<Link to={`/threads/${BOT_THREAD_ID}`} />}
        >
          <ListBullets />
        </Button>
      )}
      <Button
        variant="ghost"
        size="icon-sm"
        aria-label={t("Bot settings")}
        title={t("Bot settings")}
        render={<Link to="/settings#bot" />}
      >
        <Gear />
      </Button>
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
  const composer = useContext(BotComposerContext);
  if (!composer) throw new Error("Bot composer is missing.");
  const { send, uncertain } = composer;
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
          ? t("Bot needs your decision in Activity")
          : data?.state.paused
            ? t("Automation paused · You can still chat")
            : busy
              ? t("Working…")
              : t("Ready when you are");
  return (
    <Conversation
      threadId={BOT_THREAD_ID}
      profile={profile}
      unauthorized={unauthorized}
      publication={{
        threadAvailable: !!data?.thread,
        ready: !!data || bot.isError,
        revision: JSON.stringify(data?.messages ?? []),
        completionVersion: data?.thread?.completion?.version ?? 0,
        messages: <PublishedTranscript messages={data?.messages ?? []} />,
        status: (
          <span className={styles.status} role="status" title={status}>
            {status}
          </span>
        ),
        composer:
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
              local
              threadId={BOT_THREAD_ID}
              activity={{ state: "inactive" }}
              canRun={
                !!data?.enabled && !!data.available && !data.pending_decision
              }
              queuedInput={{
                pending: send.isPending,
                locked: uncertain,
                submit: () => send.mutate(),
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
                  <ErrorNotice
                    error={send.error || data?.state.last_error}
                    retry={uncertain ? () => send.mutate() : undefined}
                  />
                  {data && !data.available && (
                    <p>
                      {t(
                        "Check Bot's model in Settings. Another app may also own Bot processing.",
                      )}{" "}
                      <Link to="/settings#bot">{t("Bot settings")}</Link>
                    </p>
                  )}
                  {data?.pending_decision && (
                    <Link to={`/threads/${BOT_THREAD_ID}`}>
                      {t("Open Activity to respond")}
                    </Link>
                  )}
                </>
              }
            />
          ),
      }}
    />
  );
}

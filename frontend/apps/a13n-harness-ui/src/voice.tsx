import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useSyncExternalStore,
  type ReactNode,
} from "react";
import { Link } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Microphone, Stop, Waveform } from "@phosphor-icons/react";
import { Button } from "a13n-ui";
import { useTranslation } from "./i18n";
import { errorMessage } from "./i18n/error-message";
import { useTransport } from "./transport/context";
import { isConnectionError, result } from "./transport/client";
import { ConnectionNoticeContext } from "./shell/connection";
import { VoiceSession } from "./voice-session";
import styles from "./voice.module.css";

const VoiceContext = createContext<VoiceSession | null>(null);
export function VoiceProvider({ children }: { children: ReactNode }) {
  const transport = useTransport();
  const session = useMemo(() => new VoiceSession(transport), [transport]);
  useEffect(() => {
    const stop = () => session.stop();
    window.addEventListener("pagehide", stop);
    return () => {
      window.removeEventListener("pagehide", stop);
      session.stop();
    };
  }, [session]);
  return (
    <VoiceContext.Provider value={session}>{children}</VoiceContext.Provider>
  );
}
function useVoice() {
  const session = useContext(VoiceContext);
  if (!session) throw new Error("Voice provider is missing");
  const state = useSyncExternalStore(session.subscribe, session.getSnapshot);
  return { ...state, start: session.start, stop: session.stop };
}
export function useVoiceSettings() {
  const { client } = useTransport();
  return useQuery({
    queryKey: ["voice-status"],
    queryFn: ({ signal }) => result(client.GET("/api/voice", { signal })),
    staleTime: 0,
  });
}
export function VoiceIndicator() {
  const { t } = useTranslation();
  const voice = useVoice();
  const settings = useVoiceSettings();
  const running = ["starting", "connecting", "active"].includes(voice.status);
  const connectionNotice = useContext(ConnectionNoticeContext);
  const error =
    (voice.error && (voice.errorLocalizable ? t(voice.error) : voice.error)) ||
    (!(connectionNotice && isConnectionError(settings.error)) &&
      settings.error &&
      errorMessage(settings.error, t));
  if (!running && !(voice.status === "failed" && settings.data?.enabled))
    return null;
  return (
    <div className={styles.workspaceVoice}>
      <div className={styles.indicator} data-active={voice.status === "active"}>
        {running ? (
          <>
            <Link to="/live" aria-label={t("Open Live chat")}>
              <Microphone size={16} />
              <span>
                {t(voice.status === "active" ? "Microphone on" : "Connecting…")}
              </span>
            </Link>
            <Button
              size="sm"
              variant="ghost"
              onClick={voice.stop}
              aria-label={t("Stop voice")}
            >
              <Stop size={14} weight="fill" />
              <span className={styles.controlLabel}>{t("Stop")}</span>
            </Button>
          </>
        ) : (
          <Button
            size="icon-sm"
            variant="ghost"
            title={t("Retry voice")}
            onClick={() => void voice.start()}
            disabled={settings.isPending || !settings.data?.enabled}
            aria-label={t("Retry voice")}
          >
            <Microphone size={16} />
          </Button>
        )}
      </div>
      {error && (
        <p className={styles.indicatorError} role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
export function VoicePage() {
  const { t } = useTranslation();
  const voice = useVoice();
  const settings = useVoiceSettings();
  const running = ["starting", "connecting", "active"].includes(voice.status);
  const connected = voice.status === "active";
  const snapshot = voice.snapshot;
  const threadId = snapshot?.thread_id ?? settings.data?.thread_id;
  const captions = connected
    ? [
        ...(snapshot?.transcript ?? []).slice(-2),
        ...(snapshot?.user_text
          ? [{ role: "user", text: snapshot.user_text }]
          : []),
        ...(snapshot?.assistant_text
          ? [{ role: "assistant", text: snapshot.assistant_text }]
          : []),
      ].slice(-2)
    : [];
  const label = connected
    ? t("Connected")
    : running
      ? t("Connecting")
      : voice.status === "failed"
        ? t("Connection ended")
        : t("Ready when you are");
  return (
    <main className={styles.page} id="main-content">
      <header className={styles.header}>
        <Link to="/">
          <ArrowLeft size={17} /> {t("Workspace")}
        </Link>
        <span>
          <Waveform size={18} /> {t("Live")}
        </span>
        <Link to="/settings#live">{t("Settings")}</Link>
      </header>
      <section
        className={styles.center}
        data-running={running}
        aria-label={t("Voice chat")}
      >
        <h1>{t(running ? "Live chat" : "Voice chat")}</h1>
        <p className={styles.intro}>
          {t(
            running
              ? "Stay here, or keep working while we talk."
              : "Talk through a task with your agent.",
          )}
        </p>
        <div className={styles.controlArea}>
          <Button
            className={styles.control}
            disabled={
              !running && (!settings.data?.enabled || settings.isPending)
            }
            aria-label={t(running ? "Stop voice" : "Start voice")}
            onClick={() => (running ? voice.stop() : void voice.start())}
          >
            {running ? (
              <Stop size={20} weight="fill" />
            ) : (
              <Microphone size={20} />
            )}
            {t(
              running
                ? "Stop voice"
                : voice.status === "failed"
                  ? "Try again"
                  : "Start voice",
            )}
          </Button>
        </div>
        <p className={styles.status} role="status">
          <span data-active={connected} />
          {label}
        </p>
        <p className={styles.hint}>
          {t(
            connected
              ? "Microphone on - Speak naturally, interrupt anytime"
              : running
                ? "Preparing your microphone and audio connection…"
                : "Start a continuous voice chat",
          )}
        </p>
        {(voice.error || settings.error) && (
          <p className={styles.error} role="alert">
            {voice.error
              ? voice.errorLocalizable
                ? t(voice.error)
                : voice.error
              : settings.error && errorMessage(settings.error, t)}
          </p>
        )}
        {!settings.isPending && !settings.data?.enabled && !settings.error && (
          <p className={styles.hint}>
            {t("Choose a Codex model and enable voice in Settings.")}{" "}
            <Link to="/settings#live">{t("Settings")}</Link>
          </p>
        )}
        {captions.length > 0 && (
          <div
            className={styles.caption}
            aria-live="polite"
            aria-label={t("Recent captions")}
          >
            {captions.map((caption, index) => (
              <div className={styles.captionTurn} key={index}>
                <span className={styles.speaker}>
                  {t(caption.role === "user" ? "You" : "Assistant")}
                </span>
                <p>{caption.text}</p>
              </div>
            ))}
          </div>
        )}
        {threadId && (
          <Link className={styles.thread} to={`/threads/${threadId}`}>
            {t("Open tasks & decisions")}
          </Link>
        )}
      </section>
      <footer className={styles.footer}>
        <p>
          {t(
            running
              ? "Keep talking while you move around your workspace."
              : "Your microphone stays off until you press Start.",
          )}
        </p>
        <p>
          {t(
            "Audio connects directly to OpenAI. Stopping voice leaves your tasks running.",
          )}
        </p>
      </footer>
    </main>
  );
}

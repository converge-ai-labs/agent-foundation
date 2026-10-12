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
function useVoiceSettings() {
  const { client } = useTransport();
  return useQuery({
    queryKey: ["voice-status"],
    queryFn: ({ signal }) => result(client.GET("/api/voice", { signal })),
    staleTime: 0,
  });
}
export function VoiceIndicator() {
  const voice = useVoice();
  const settings = useVoiceSettings();
  const running = ["starting", "connecting", "active"].includes(voice.status);
  const connectionNotice = useContext(ConnectionNoticeContext);
  const error =
    voice.error ||
    (!(connectionNotice && isConnectionError(settings.error)) &&
      settings.error?.message);
  return (
    <div className={styles.workspaceVoice}>
      <div className={styles.indicator} data-active={voice.status === "active"}>
        {running ? (
          <>
            <Link to="/live" aria-label="Open Live conversation">
              <Microphone size={16} />
              <span>
                {voice.status === "active" ? "Microphone on" : "Connecting…"}
              </span>
            </Link>
            <Button
              size="sm"
              variant="ghost"
              onClick={voice.stop}
              aria-label="Stop voice"
            >
              <Stop size={14} weight="fill" />
              <span className={styles.controlLabel}>Stop</span>
            </Button>
          </>
        ) : !settings.isPending &&
          !settings.data?.enabled &&
          !settings.error ? (
          <Link to="/settings#live">
            <Microphone size={16} /> Set up voice
          </Link>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => void voice.start()}
            disabled={settings.isPending || !settings.data?.enabled}
            aria-label="Start voice"
          >
            <Microphone size={16} />
            {voice.status === "failed" ? "Retry voice" : "Start voice"}
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
    ? "Connected"
    : running
      ? "Connecting"
      : voice.status === "failed"
        ? "Connection ended"
        : "Ready when you are";
  return (
    <main className={styles.page} id="main-content">
      <header className={styles.header}>
        <Link to="/">
          <ArrowLeft size={17} /> Workspace
        </Link>
        <span>
          <Waveform size={18} /> Live
        </span>
        <Link to="/settings#live">Settings</Link>
      </header>
      <section
        className={styles.center}
        data-running={running}
        aria-label="Voice conversation"
      >
        <p className={styles.eyebrow}>HARNESS UI · LIVE</p>
        <h1>
          {running ? (
            "Live conversation"
          ) : (
            <>
              A little less typing.
              <br />A little more conversation.
            </>
          )}
        </h1>
        <p className={styles.intro}>
          {running
            ? "Stay here, or keep working while we talk."
            : "Talk things through. Put your agents to work."}
        </p>
        <div className={styles.controlArea} data-active={connected}>
          <div className={styles.halo} aria-hidden="true" />
          <button
            className={styles.control}
            disabled={
              !running && (!settings.data?.enabled || settings.isPending)
            }
            aria-label={running ? "Stop voice" : "Start voice"}
            onClick={() => (running ? voice.stop() : void voice.start())}
          >
            {running ? (
              <Stop size={34} weight="fill" />
            ) : (
              <Microphone size={40} weight="light" />
            )}
          </button>
        </div>
        <p className={styles.status} role="status">
          <span data-active={connected} />
          {label}
        </p>
        <p className={styles.hint}>
          {connected
            ? "Microphone on · Speak naturally, interrupt anytime"
            : running
              ? "Preparing your microphone and audio connection…"
              : "Start a continuous voice conversation"}
        </p>
        {(voice.error || settings.error) && (
          <p className={styles.error} role="alert">
            {voice.error || settings.error?.message}
          </p>
        )}
        {!settings.isPending && !settings.data?.enabled && !settings.error && (
          <p className={styles.hint}>
            Choose a Codex Model and enable{" "}
            <Link to="/settings#live">Live in Settings</Link>.
          </p>
        )}
        <div
          className={styles.caption}
          aria-live="polite"
          aria-label="Recent captions"
        >
          {captions.map((caption, index) => (
            <div className={styles.captionTurn} key={index}>
              <span className={styles.speaker}>
                {caption.role === "user" ? "You" : "Assistant"}
              </span>
              <p>{caption.text}</p>
            </div>
          ))}
        </div>
        {threadId && (
          <Link className={styles.thread} to={`/threads/${threadId}`}>
            Open tasks & decisions
          </Link>
        )}
      </section>
      <footer className={styles.footer}>
        <p>
          {running
            ? "Keep talking while you move around your workspace."
            : "Your microphone stays off until you press Start."}
        </p>
        <p>
          Audio connects directly to OpenAI. Stopping voice leaves your tasks
          running.
        </p>
      </footer>
    </main>
  );
}

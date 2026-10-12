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
import { result } from "./transport/client";
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
export function VoiceIndicator() {
  const voice = useVoice();
  if (!["starting", "connecting", "active"].includes(voice.status)) return null;
  return (
    <div className={styles.indicator}>
      <Link to="/live">
        <Microphone size={16} />{" "}
        {voice.status === "active" ? "Microphone on" : "Starting voice…"}
      </Link>
      <Button size="sm" variant="ghost" onClick={voice.stop}>
        Stop voice
      </Button>
    </div>
  );
}
export function VoicePage() {
  const voice = useVoice();
  const { client } = useTransport();
  const settings = useQuery({
    queryKey: ["voice-status"],
    queryFn: ({ signal }) => result(client.GET("/api/voice", { signal })),
    staleTime: 0,
  });
  const running = ["starting", "connecting", "active"].includes(voice.status);
  const connected = voice.status === "active";
  const snapshot = voice.snapshot;
  const threadId = snapshot?.thread_id ?? settings.data?.thread_id;
  const lastTurn = snapshot?.transcript?.at(-1);
  const caption =
    snapshot?.assistant_text ||
    snapshot?.user_text ||
    (connected ? lastTurn?.text : "");
  const label = connected
    ? "Listening"
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
      <section className={styles.center} aria-label="Voice conversation">
        <p className={styles.eyebrow}>HARNESS UI · LIVE</p>
        <h1>
          A little less typing.
          <br />A little more conversation.
        </h1>
        <p className={styles.intro}>
          Talk things through. Put your agents to work.
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
        <div className={styles.caption} aria-live="polite">
          {caption}
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

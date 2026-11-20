import { PhoneDisconnectIcon, WaveformIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { Composer } from "./composer";
import { GuidanceMessage } from "./transcript/user-message";

interface Selection {
  agentId?: string;
  revisionId?: string | null;
  threadId?: string;
}

interface Availability {
  realtime: boolean;
  can_start: boolean;
  accepts_text: boolean;
  reason?: string | null;
}

export interface RealtimeReceipt {
  session_id: string;
  thread_id: string;
  run_id: string;
}

export function useRealtimeAvailability(
  { agentId, revisionId, threadId }: Selection,
  busy = false,
) {
  const { workspace, can } = useWorkspace();
  const client = useClient();
  const identity = [workspace.id, agentId, revisionId, threadId];
  return useQuery<Availability>({
    queryKey: ["realtime", ...identity, busy],
    placeholderData: (previous, query) =>
      query?.queryKey
        .slice(1, 5)
        .every((value, index) => value === identity[index])
        ? previous
        : undefined,
    enabled: !!agentId && can("run"),
    retry: false,
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/realtime", {
          params: {
            query: {
              agent_id: agentId!,
              agent_revision_id: revisionId ?? undefined,
              thread_id: threadId,
            },
          },
          signal,
        })
        .then(data)
        .then((value): Availability => ({
          realtime: value.realtime ?? false,
          can_start: value.can_start ?? false,
          accepts_text: value.accepts_text ?? false,
          reason: value.reason,
        })),
  }).data;
}

type Message = {
  message_id: string;
  role: "user" | "assistant";
  text: string;
  speech?: boolean;
};
type Event =
  | ({ type: "accepted" } & RealtimeReceipt)
  | { type: "ready"; audio_hz: number; accepts_text: boolean }
  | ({ type: "transcript"; final: boolean } & Omit<Message, "speech">)
  | { type: "error"; message: string };

export function RealtimeComposer({
  agentId,
  revisionId,
  threadId,
  displayedRunId,
  availability,
  onFinished,
  agentName,
  initial,
}: Selection & {
  availability: Availability;
  displayedRunId?: string;
  agentName?: string;
  initial?: Schema["MessagePayload"];
  onFinished: (receipt: RealtimeReceipt) => void;
}) {
  const { t } = useTranslation();
  const { workspace } = useWorkspace();
  const [phase, setPhase] = useState<
    "idle" | "connecting" | "ready" | "ending"
  >("idle");
  const [mic, setMic] = useState(false);
  const [error, setError] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [previewRunId, setPreviewRunId] = useState<string>();
  const [acceptsText, setAcceptsText] = useState(availability.accepts_text);
  const socket = useRef<WebSocket | null>(null);
  const cleanup = useRef<(() => void) | null>(null);

  useEffect(() => () => cleanup.current?.(), []);

  async function start(audio: boolean, firstText?: string) {
    if (!agentId || cleanup.current || !availability.can_start)
      throw new Error(t("Realtime connection failed"));
    const context = new AudioContext({ sampleRate: 24000 });
    let settleFirst: ((error?: Error) => void) | undefined;
    let input: MediaStream | undefined;
    let capture: AudioWorkletNode | undefined;
    let connection: WebSocket | undefined;
    let ready = false;
    let next = 0;
    let receipt: RealtimeReceipt | undefined;
    let disposed = false;
    const dispose = () => {
      disposed = true;
      input?.getTracks().forEach((track) => track.stop());
      capture?.disconnect();
      if (connection && connection.readyState < WebSocket.CLOSING)
        connection.close();
      void context.close();
      settleFirst?.(new Error(t("Realtime connection failed")));
      socket.current = null;
      cleanup.current = null;
    };
    cleanup.current = dispose;
    setError("");
    setPhase("connecting");
    setMic(audio);
    setMessages([]);
    setPreviewRunId(undefined);
    try {
      await context.resume();
      if (audio) {
        input = await navigator.mediaDevices.getUserMedia({
          audio: {
            channelCount: 1,
            echoCancellation: true,
            noiseSuppression: true,
          },
        });
        if (disposed) {
          input.getTracks().forEach((track) => track.stop());
          return;
        }
        await context.audioWorklet.addModule("/realtime-capture.js");
        if (disposed) return;
        capture = new AudioWorkletNode(context, "realtime-capture");
        context.createMediaStreamSource(input).connect(capture);
        capture.connect(context.destination);
        capture.port.onmessage = ({ data }: MessageEvent<ArrayBuffer>) => {
          if (
            ready &&
            connection?.readyState === WebSocket.OPEN &&
            connection.bufferedAmount < 64 * 1024
          )
            connection.send(data);
        };
      }
      if (disposed) return;
      const url = new URL(
        `/api/v1/workspaces/${workspace.id}/realtime`,
        window.location.href,
      );
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      const connected = new WebSocket(url);
      connection = connected;
      socket.current = connected;
      connected.binaryType = "arraybuffer";
      const firstMessage = firstText
        ? new Promise<void>((resolve, reject) => {
            settleFirst = (error) => (error ? reject(error) : resolve());
          })
        : undefined;
      connected.onopen = () =>
        connected.send(
          JSON.stringify({
            agent_id: agentId,
            agent_revision_id: revisionId,
            thread_id: threadId,
            audio,
          }),
        );
      connected.onmessage = ({ data }: MessageEvent<ArrayBuffer | string>) => {
        if (typeof data !== "string") {
          const pcm = new Int16Array(data);
          const buffer = context.createBuffer(1, pcm.length, 24000);
          const samples = buffer.getChannelData(0);
          for (let i = 0; i < pcm.length; i++) samples[i] = pcm[i]! / 32768;
          const playback = context.createBufferSource();
          playback.buffer = buffer;
          playback.connect(context.destination);
          next = Math.max(next, context.currentTime + 0.02);
          playback.start(next);
          next += buffer.duration;
          return;
        }
        const event = JSON.parse(data) as Event;
        if (event.type === "accepted") {
          receipt = event;
          setPreviewRunId(event.run_id);
        }
        if (event.type === "ready") {
          ready = true;
          setAcceptsText(event.accepts_text);
          setPhase("ready");
          if (firstText && event.accepts_text) {
            connected.send(JSON.stringify({ type: "text", text: firstText }));
            addText(firstText);
            settleFirst?.();
          } else {
            settleFirst?.(new Error(t("Realtime connection failed")));
          }
        }
        if (event.type === "transcript") {
          const { message_id, role, text } = event;
          const message: Message = { message_id, role, text, speech: true };
          setMessages((items) => {
            if (items.some((item) => item.message_id === message_id))
              return items.map((item) =>
                item.message_id === message_id ? message : item,
              );
            return text.trim() ? [...items, message] : items;
          });
        }
        if (event.type === "error") {
          setError(event.message);
          settleFirst?.(new Error(event.message));
        }
      };
      connected.onerror = () => setError(t("Realtime connection failed"));
      connected.onclose = (event) => {
        if (disposed) return;
        dispose();
        setPhase("idle");
        if (!ready && event.code !== 1000)
          setError((value) => value || t("Realtime connection failed"));
        if (receipt && ready && event.code === 1000) onFinished(receipt);
      };
      await firstMessage;
    } catch (cause) {
      if (!disposed) dispose();
      setPhase("idle");
      throw cause;
    }
  }

  function addText(text: string) {
    const message: Message = {
      message_id: crypto.randomUUID(),
      role: "user",
      text,
    };
    setMessages((items) => [...items, message]);
  }

  async function submit(payload: Schema["MessagePayload"]) {
    const text = payload.content
      .flatMap((part) => (part.type === "text" ? [part.text.trim()] : []))
      .join("\n\n");
    if (!text || text.length > 8192)
      throw new Error(
        t("Realtime messages must be between 1 and 8,192 characters."),
      );
    if (phase === "idle") {
      await start(false, text);
    } else if (
      phase === "ready" &&
      socket.current?.readyState === WebSocket.OPEN
    ) {
      socket.current.send(JSON.stringify({ type: "text", text }));
      addText(text);
    } else {
      throw new Error(t("Realtime connection failed"));
    }
  }

  function stop() {
    if (phase === "ready" && socket.current?.readyState === WebSocket.OPEN) {
      socket.current.send(JSON.stringify({ type: "stop" }));
      setPhase("ending");
    } else {
      cleanup.current?.();
      setPhase("idle");
    }
  }

  return (
    <>
      {(!displayedRunId || previewRunId !== displayedRunId) &&
        messages.some(({ text }) => text.trim()) && (
          <div className="grid max-h-60 gap-2 overflow-auto" aria-live="polite">
            {messages.map(({ message_id, role, text, speech }) =>
              !text.trim() ? null : role === "user" && speech ? (
                <GuidanceMessage key={message_id} text={text} />
              ) : (
                <p
                  key={message_id}
                  className={role === "user" ? "text-muted-foreground" : ""}
                >
                  {text}
                </p>
              ),
            )}
          </div>
        )}
      <Composer
        agentName={agentName}
        initial={initial}
        textInput={acceptsText}
        attachmentsEnabled={false}
        submitDisabled={
          (phase === "idle" && !availability.can_start) ||
          phase === "connecting" ||
          phase === "ending"
        }
        submit={submit}
        emptyAction={{
          label:
            phase === "idle" ? t("Start voice chat") : t("End realtime chat"),
          icon:
            phase === "idle" ? (
              <WaveformIcon size={17} weight="bold" />
            ) : (
              <PhoneDisconnectIcon size={16} />
            ),
          disabled:
            phase === "ending" || (phase === "idle" && !availability.can_start),
          onClick: () => {
            if (phase !== "idle") {
              stop();
            } else {
              void start(true).catch((cause: unknown) => {
                setError(
                  cause instanceof Error
                    ? cause.message
                    : t("Realtime connection failed"),
                );
              });
            }
          },
        }}
        options={
          phase !== "idle" && (
            <span className="text-xs text-muted-foreground" role="status">
              {phase === "connecting" && t("Connecting…")}
              {phase === "ready" && mic && t("Microphone on")}
              {phase === "ending" && t("Saving conversation…")}
            </span>
          )
        }
      />
      {(error || (phase === "idle" && availability.reason)) && (
        <p role="status" className="text-sm text-muted-foreground">
          {error || availability.reason}
        </p>
      )}
    </>
  );
}

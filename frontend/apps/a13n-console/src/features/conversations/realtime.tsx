import {
  PhoneDisconnectIcon,
  SpeakerSlashIcon,
  WaveformIcon,
} from "@phosphor-icons/react";
import { Button } from "a13n-ui";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useLocation } from "react-router";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { delay } from "../../service-client/transport";
import { data, type Schema } from "../../shared/api";
import { Composer } from "./composer";
import captureModule from "./realtime-capture.js?url&no-inline";
import { invalidateConversation, runPath } from "./api";

interface Selection {
  agentId?: string;
  revisionId?: string | null;
  model?: string;
  threadId?: string;
  forkRunId?: string;
}

interface Availability {
  realtime: boolean;
  can_start: boolean;
  accepts_text: boolean;
  reason?: string | null;
  transports?: readonly ("websocket" | "webrtc")[];
}

export interface RealtimeReceipt {
  session_id: string;
  thread_id: string;
  run_id: string;
}

export function useRealtimeAvailability({
  agentId,
  revisionId,
  threadId,
  forkRunId,
  model,
}: Selection) {
  const { workspace, can } = useWorkspace();
  const client = useClient();
  return useQuery<Availability>({
    queryKey: [
      "realtime",
      workspace.id,
      agentId,
      revisionId,
      threadId,
      forkRunId,
      model,
    ],
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
              fork_run_id: forkRunId,
              model,
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
          transports: value.transports,
        })),
  });
}

type Approval = {
  type: "approval_required";
  call_id: string;
  name: string;
  arguments: string;
};

type Event =
  | Approval
  | { type: "approval_expired"; call_id: string }
  | ({ type: "accepted" } & RealtimeReceipt)
  | { type: "sealed"; run_id: string; status: string }
  | {
      type:
        | "barge_in"
        | "interrupted"
        | "playback_start"
        | "playback_end"
        | "input_discontinuity"
        | "draining";
    }
  | { type: "playback_position"; offset: number }
  | { type: "answer"; sdp: string }
  | {
      type: "ready";
      accepts_text: boolean;
      supports_interruption: boolean;
      audio_input_hz?: number;
      audio_output_hz?: number;
      audio_hz?: number;
    }
  | { type: "ending"; reason: string }
  | { type: "error"; message: string };

interface CallOptions extends Selection {
  availability: Availability;
  freshEnvironments?: boolean;
  setup?: () => Promise<
    Pick<
      Schema["RealtimeSessionCreate"],
      "options" | "environments" | "memories" | "memory"
    >
  >;
  onAccepted: (receipt: RealtimeReceipt) => void;
}

function useRealtimeCall() {
  const { t } = useTranslation();
  const { workspace, basePath, can } = useWorkspace();
  const canRun = can("run");
  const client = useClient();
  const cache = useQueryClient();
  const interruptPlayback = useRef<(() => void) | null>(null);
  const [phase, setPhase] = useState<
    "idle" | "connecting" | "ready" | "ending"
  >("idle");
  const [error, setError] = useState("");
  const [owner, setOwner] = useState<string>();
  const [activeRun, setActiveRun] = useState<{
    id: string;
    availability: Availability;
  }>();
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [supportsInterruption, setSupportsInterruption] = useState(false);
  const [acceptsText, setAcceptsText] = useState(true);
  const socket = useRef<WebSocket | null>(null);
  const cleanup = useRef<(() => void) | null>(null);
  const recoverClose = useRef<(() => void) | null>(null);
  const endingTimer = useRef<ReturnType<typeof setTimeout> | undefined>(
    undefined,
  );
  const stopMedia = useRef<(() => void) | null>(null);

  useEffect(() => () => cleanup.current?.(), []);
  useEffect(() => {
    if (!canRun) cleanup.current?.();
  }, [canRun]);

  const dialingOwner = useRef<string | undefined>(undefined);
  const runRoute = useRef<string | undefined>(undefined);
  const { pathname } = useLocation();
  useEffect(() => {
    if (runRoute.current && pathname !== runRoute.current) cleanup.current?.();
  }, [pathname]);

  async function start(
    ownerId: string,
    {
      agentId,
      revisionId,
      threadId,
      forkRunId,
      freshEnvironments,
      setup,
      availability,
      onAccepted,
    }: CallOptions,
    firstText?: string,
    displayedRunId?: string,
  ): Promise<void | false> {
    if (!agentId || cleanup.current || !availability.can_start)
      throw new Error(t("Realtime connection failed"));
    const useWebRTC =
      availability.transports?.includes("webrtc") &&
      typeof RTCPeerConnection !== "undefined";
    let context: AudioContext;
    const abort = new AbortController();
    let settleFirst: ((result?: Error | false) => void) | undefined;
    let input: MediaStream | undefined;
    let capture: AudioWorkletNode | undefined;
    let peer: RTCPeerConnection | undefined;
    let speaker: HTMLAudioElement | undefined;
    let offer: string | undefined;
    let startupTimer: ReturnType<typeof setTimeout> | undefined;
    let connection: WebSocket | undefined;
    let ready = false;
    let serverReady: Extract<Event, { type: "ready" }> | undefined;
    let audioInputHz = 24000;
    let audioOutputHz = 24000;
    let sealed = false;
    let interrupting = false;
    let playedBytes = 0;
    let nextOffset = 0;
    let remoteLevel: AnalyserNode | undefined;
    const remoteSamples = new Float32Array(512);
    const playing = new Map<
      AudioBufferSourceNode,
      { start: number; samples: number; offset: number }
    >();
    let next = 0;
    const playbackTime = () =>
      context.getOutputTimestamp?.().contextTime ?? context.currentTime;
    const hasPlayback = () => {
      if (!useWebRTC) return next > playbackTime();
      if (!remoteLevel || speaker?.muted) return false;
      remoteLevel.getFloatTimeDomainData(remoteSamples);
      return remoteSamples.some((sample) => Math.abs(sample) >= 0.02);
    };
    const interrupt = () => {
      if (
        !ready ||
        interrupting ||
        mediaStopped ||
        connection?.readyState !== WebSocket.OPEN
      )
        return;
      interrupting = true;
      const playedUntil = playbackTime();
      for (const [source, chunk] of playing) {
        const samples = Math.min(
          chunk.samples,
          Math.max(0, Math.floor((playedUntil - chunk.start) * audioOutputHz)),
        );
        if (samples) playedBytes = chunk.offset + samples * 2;
        source.onended = null;
        source.stop();
      }
      playing.clear();
      next = 0;
      if (speaker) speaker.muted = true;
      connection.send(
        JSON.stringify({ type: "interrupt", played_bytes: playedBytes }),
      );
    };
    let receipt: RealtimeReceipt | undefined;
    let disposed = false;
    let mediaStopped = false;
    stopMedia.current = () => {
      if (mediaStopped) return;
      mediaStopped = true;
      input?.getTracks().forEach((track) => track.stop());
      capture?.disconnect();
      if (speaker) {
        speaker.pause();
        speaker.srcObject = null;
      }
      void context?.close();
    };
    const dispose = () => {
      disposed = true;
      dialingOwner.current = undefined;
      runRoute.current = undefined;
      setActiveRun(undefined);
      setPhase("idle");
      interruptPlayback.current = null;
      clearTimeout(endingTimer.current);
      recoverClose.current = null;
      abort.abort();
      clearTimeout(startupTimer);
      stopMedia.current?.();
      stopMedia.current = null;
      peer?.close();
      if (connection && connection.readyState < WebSocket.CLOSING)
        connection.close();
      settleFirst?.(false);
      socket.current = null;
      cleanup.current = null;
    };
    const failMedia = (message: string) => {
      if (disposed || mediaStopped) return;
      setError(message);
      if (connection) {
        stopMedia.current?.();
        setPhase("ending");
        connection.close();
        return;
      }
      dispose();
    };
    const startConversation = () => {
      if (
        !serverReady ||
        ready ||
        mediaStopped ||
        disposed ||
        (peer && peer.connectionState !== "connected") ||
        connection?.readyState !== WebSocket.OPEN
      )
        return;
      audioInputHz = serverReady.audio_input_hz ?? audioInputHz;
      audioOutputHz =
        serverReady.audio_output_hz ?? serverReady.audio_hz ?? audioOutputHz;
      try {
        capture = new AudioWorkletNode(context, "realtime-capture", {
          processorOptions: { pcm: !useWebRTC, inputHz: audioInputHz },
        });
        context.createMediaStreamSource(input!).connect(capture);
        capture.connect(context.destination);
        capture.port.onmessage = ({
          data,
        }: MessageEvent<ArrayBuffer | "speech">) => {
          if (
            ready &&
            !mediaStopped &&
            connection?.readyState === WebSocket.OPEN
          ) {
            if (data === "speech") {
              if (hasPlayback()) interrupt();
              return;
            }
            if (connection.bufferedAmount >= 64 * 1024) {
              failMedia(t("Realtime connection failed"));
              return;
            }
            connection.send(data);
          }
        };
      } catch {
        failMedia(t("Realtime connection failed"));
        return;
      }
      ready = true;
      clearTimeout(startupTimer);
      if (peer) {
        input?.getAudioTracks().forEach((track) => {
          track.enabled = true;
        });
        if (speaker) speaker.muted = false;
      }
      setAcceptsText(serverReady.accepts_text);
      setSupportsInterruption(serverReady.supports_interruption);
      setPhase("ready");
      if (firstText && serverReady.accepts_text) {
        settleFirst?.();
      } else {
        settleFirst?.(new Error(t("Realtime connection failed")));
      }
    };
    interruptPlayback.current = interrupt;
    cleanup.current = dispose;
    setError("");
    setPhase("connecting");
    setOwner(ownerId);
    dialingOwner.current = ownerId;
    if (displayedRunId) setActiveRun({ id: displayedRunId, availability });
    setApprovals([]);
    try {
      context = new AudioContext();
      await context.resume();
      if (disposed) return false;
      input = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });
      if (disposed) {
        input.getTracks().forEach((track) => track.stop());
        return false;
      }
      await context.audioWorklet.addModule(captureModule);
      if (disposed) return false;
      if (useWebRTC) {
        const rtc = new RTCPeerConnection();
        peer = rtc;
        const playback = new Audio();
        speaker = playback;
        playback.autoplay = true;
        playback.muted = true;
        input.getAudioTracks().forEach((track) => {
          track.enabled = false;
          rtc.addTrack(track, input!);
          track.onended = () => failMedia(t("Realtime connection failed"));
        });
        rtc.ontrack = (event) => {
          if (disposed || mediaStopped) return;
          const remote = event.streams[0] ?? new MediaStream([event.track]);
          playback.srcObject = remote;
          remoteLevel = context.createAnalyser();
          remoteLevel.fftSize = remoteSamples.length;
          context.createMediaStreamSource(remote).connect(remoteLevel);
          void playback
            .play()
            .catch(() => failMedia(t("Realtime connection failed")));
        };
        rtc.onconnectionstatechange = () => {
          if (rtc.connectionState === "connected") startConversation();
          if (
            rtc.connectionState === "failed" ||
            rtc.connectionState === "closed"
          )
            failMedia(t("Realtime connection failed"));
        };
        await rtc.setLocalDescription(await rtc.createOffer());
        await waitForIce(
          rtc,
          abort.signal,
          t("Audio network setup timed out."),
        );
        if (disposed) return false;
        offer = rtc.localDescription?.sdp;
        if (!offer) throw new Error(t("Realtime connection failed"));
      }
      const ticket = await client
        .workspace(workspace.id)
        .POST("/api/v1/realtime/sessions", {
          body: {
            ...(await setup?.()),
            agent_id: agentId,
            agent_revision_id: revisionId,
            thread_id: threadId,
            fork_run_id: forkRunId,
            fresh_environments: freshEnvironments,
            audio: true,
            draft: firstText,
            transport: offer ? "webrtc" : "websocket",
          },
          signal: abort.signal,
        })
        .then(data);
      if (disposed) return false;
      audioInputHz = ticket.audio_input_hz ?? audioInputHz;
      audioOutputHz = ticket.audio_output_hz ?? audioOutputHz;
      receipt = {
        session_id: ticket.session_id,
        thread_id: ticket.thread_id,
        run_id: ticket.run_id,
      };
      const url = new URL(ticket.websocket_path, window.location.href);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      const connected = new WebSocket(url, [ticket.subprotocol, ticket.ticket]);
      connection = connected;
      socket.current = connected;
      connected.binaryType = "arraybuffer";
      const firstMessage = firstText
        ? new Promise<void | false>((resolve, reject) => {
            settleFirst = (result) =>
              result instanceof Error ? reject(result) : resolve(result);
          })
        : undefined;
      if (useWebRTC)
        startupTimer = setTimeout(
          () => failMedia(t("Realtime connection failed")),
          30_000,
        );
      connected.onopen = () => {
        if (offer)
          connected.send(JSON.stringify({ type: "offer", sdp: offer }));
      };
      connected.onmessage = ({ data }: MessageEvent<ArrayBuffer | string>) => {
        if (typeof data !== "string") {
          const offset = nextOffset;
          nextOffset += data.byteLength;
          if (useWebRTC || mediaStopped || interrupting || !serverReady) return;
          const pcm = new Int16Array(data);
          const buffer = context.createBuffer(1, pcm.length, audioOutputHz);
          const samples = buffer.getChannelData(0);
          for (let i = 0; i < pcm.length; i++) samples[i] = pcm[i]! / 32768;
          const playback = context.createBufferSource();
          playback.buffer = buffer;
          playback.connect(context.destination);
          next = Math.max(next, context.currentTime + 0.02);
          const playedUntil = playbackTime();
          for (const [source, chunk] of playing) {
            if (chunk.start + chunk.samples / audioOutputHz > playedUntil)
              break;
            playedBytes = chunk.offset + chunk.samples * 2;
            playing.delete(source);
          }
          playing.set(playback, { start: next, samples: pcm.length, offset });
          playback.start(next);
          next += buffer.duration;
          return;
        }
        const event = JSON.parse(data) as Event;
        if (event.type === "sealed" && receipt?.run_id === event.run_id) {
          sealed = true;
          setApprovals([]);
        }
        if (event.type === "playback_position") nextOffset = event.offset;
        if (event.type === "barge_in" && hasPlayback()) interrupt();
        if (event.type === "interrupted") interrupting = false;
        if (
          (event.type === "playback_start" || event.type === "playback_end") &&
          speaker &&
          !mediaStopped
        ) {
          speaker.muted = false;
        }
        if (event.type === "draining" || event.type === "ending") {
          stopMedia.current?.();
          setPhase("ending");
        }
        if (event.type === "input_discontinuity")
          setError(t("Voice input was interrupted. Please repeat that part."));
        if (event.type === "accepted") {
          receipt = event;
          dialingOwner.current = undefined;
          runRoute.current = runPath(basePath, event);
          setOwner(event.run_id);
          setActiveRun({ id: event.run_id, availability });
          onAccepted(event);
        }
        if (event.type === "answer" && peer) {
          void peer
            .setRemoteDescription({ type: "answer", sdp: event.sdp })
            .catch(() => failMedia(t("Realtime connection failed")));
        }
        if (event.type === "ready") {
          serverReady = event;
          startConversation();
        }
        if (event.type === "approval_required") {
          setApprovals((items) =>
            items.some((item) => item.call_id === event.call_id)
              ? items
              : [...items, event],
          );
        }
        if (event.type === "approval_expired") {
          setApprovals((items) =>
            items.filter((item) => item.call_id !== event.call_id),
          );
        }
        if (event.type === "error") {
          setError(event.message);
          // The close handler checks whether the ticket already created a Run.
          settleFirst?.(false);
        }
      };
      connected.onerror = () => setError(t("Realtime connection failed"));
      let closing = false;
      const finish = async (code: number) => {
        if (disposed || closing) return;
        closing = true;
        stopMedia.current?.();
        clearTimeout(startupTimer);
        clearTimeout(endingTimer.current);
        setApprovals([]);
        setPhase("ending");
        if (!ready || code !== 1000)
          setError((value) => value || t("Realtime connection failed"));
        try {
          if (!receipt) return;
          if (!sealed) {
            // The Run page follows an accepted or running Run through lease recovery.
            const api = client.workspace(workspace.id);
            const signal = AbortSignal.any([
              abort.signal,
              AbortSignal.timeout(20000),
            ]);
            // Retry an early 404 quickly and allow for the 10-second redemption window.
            const retryDelays = [500, 1000, 2000, 3000, 4000];
            for (let attempt = 0; ; attempt++) {
              const run = await api.GET("/api/v1/runs/{run_id}", {
                params: { path: { run_id: receipt.run_id } },
                signal,
              });
              if (run.response.status !== 404) {
                data(run);
                break;
              }
              const retryDelay = retryDelays[attempt];
              if (retryDelay === undefined) return;
              await delay(retryDelay, signal);
            }
          }
          if (!disposed) {
            void invalidateConversation(cache, workspace.id, {
              sessionId: receipt.session_id,
              threadId: receipt.thread_id,
              runId: receipt.run_id,
            });
            if (!runRoute.current) onAccepted(receipt);
          }
        } catch {
          if (!disposed) setError(t("Realtime connection failed"));
        } finally {
          if (!disposed) {
            void cache.invalidateQueries({
              queryKey: ["realtime", workspace.id, agentId],
            });
            dispose();
          }
        }
      };
      connected.onclose = (event) => void finish(event.code);
      recoverClose.current = () => {
        connection?.close();
        void finish(1000);
      };
      return firstMessage;
    } catch (cause) {
      if (disposed) return false;
      dispose();
      throw cause;
    }
  }

  function sendText(text: string) {
    if (phase !== "ready" || socket.current?.readyState !== WebSocket.OPEN)
      throw new Error(t("Realtime connection failed"));
    socket.current.send(JSON.stringify({ type: "text", text }));
  }

  function decide(callId: string, approve: boolean) {
    if (phase !== "ready" || socket.current?.readyState !== WebSocket.OPEN)
      return;
    socket.current.send(
      JSON.stringify({ type: "approval", call_id: callId, approve }),
    );
    setApprovals((items) => items.filter((item) => item.call_id !== callId));
  }

  function stop() {
    if (phase === "ready" && socket.current?.readyState === WebSocket.OPEN) {
      const connection = socket.current;
      connection.send(JSON.stringify({ type: "stop" }));
      endingTimer.current = setTimeout(() => recoverClose.current?.(), 10000);
      stopMedia.current?.();
      setPhase("ending");
    } else {
      cleanup.current?.();
      setPhase("idle");
    }
  }

  return {
    phase,
    owner,
    activeRun,
    error,
    setError,
    approvals,
    acceptsText,
    supportsInterruption,
    start,
    sendText,
    stop,
    decide,
    interruptPlayback,
    dialingOwner,
    cleanup,
  };
}

const CallContext = createContext<ReturnType<typeof useRealtimeCall> | null>(
  null,
);

export function RealtimeCallProvider({ children }: { children: ReactNode }) {
  const call = useRealtimeCall();
  return <CallContext.Provider value={call}>{children}</CallContext.Provider>;
}

export function useActiveRealtimeAvailability(runId: string) {
  const call = useContext(CallContext);
  return call?.activeRun?.id === runId
    ? call.activeRun.availability
    : undefined;
}

type VoiceComposerOptions = CallOptions & {
  displayedRunId?: string;
  controls?: ReactNode;
  disabled?: boolean;
  agentName?: string;
  initial?: Schema["MessagePayload"];
};

export function RealtimeComposer(options: VoiceComposerOptions) {
  return <ConversationComposer voice={options} />;
}

export function ConversationComposer({
  voice,
  children,
}: {
  voice?: VoiceComposerOptions;
  children?: ReactNode;
}) {
  const controls = useVoiceComposer(voice);
  return (
    <>
      {controls?.approvals}
      {controls?.editor ?? children}
      {controls?.status}
    </>
  );
}

function useVoiceComposer(voice?: VoiceComposerOptions) {
  const { t } = useTranslation();
  const id = useId();
  const call = useContext(CallContext);
  const dialingOwner = call?.dialingOwner;
  const cleanup = call?.cleanup;
  useEffect(
    () => () => {
      if (dialingOwner?.current === id) cleanup?.current?.();
    },
    [id, dialingOwner, cleanup, voice?.agentId],
  );
  if (!voice) return;
  if (!call) throw new Error(t("Realtime conversation is unavailable."));
  const {
    displayedRunId,
    controls,
    disabled,
    agentName,
    initial,
    availability,
  } = voice;
  const {
    owner,
    sendText,
    supportsInterruption,
    interruptPlayback,
    stop,
    decide,
    setError,
  } = call;
  const mine = owner === id || (!!displayedRunId && owner === displayedRunId);
  const phase = mine ? call.phase : "idle";
  const error = mine ? call.error : "";
  const approvals = mine ? call.approvals : [];
  const acceptsText =
    phase === "ready" ? call.acceptsText : availability.accepts_text;
  const start = (text?: string) => call.start(id, voice, text, displayedRunId);
  async function submit(payload: Schema["MessagePayload"]) {
    const text = payload.content
      .flatMap((part) => (part.type === "text" ? [part.text.trim()] : []))
      .join("\n\n");
    if (!text || text.length > 8192)
      throw new Error(
        t("Realtime messages must be between 1 and 8,192 characters."),
      );
    if (phase === "idle") return await start(text);
    sendText(text);
  }

  return {
    approvals: approvals.map((approval) => (
      <section
        key={approval.call_id}
        aria-label={t("Approval required")}
        className="grid gap-2 rounded-xl border p-3"
      >
        <p className="text-sm font-medium">{approval.name}</p>
        <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-words text-xs">
          {approval.arguments}
        </pre>
        <div className="flex justify-end gap-2">
          <Button
            type="button"
            variant="outline"
            disabled={phase !== "ready"}
            onClick={() => decide(approval.call_id, false)}
          >
            {t("Deny")}
          </Button>
          <Button
            type="button"
            disabled={phase !== "ready"}
            onClick={() => decide(approval.call_id, true)}
          >
            {t("Approve once")}
          </Button>
        </div>
      </section>
    )),
    editor: (
      <Composer
        options={phase === "idle" ? controls : undefined}
        agentName={agentName}
        initial={initial}
        textInput={acceptsText}
        attachmentsEnabled={false}
        submitDisabled={
          (phase === "idle" &&
            (disabled || !availability.can_start || call.phase !== "idle")) ||
          phase === "connecting" ||
          phase === "ending"
        }
        submit={submit}
        label={
          phase === "ready"
            ? t("Send text in voice chat")
            : t("Start voice chat")
        }
        submitIcon={<WaveformIcon size={17} weight="bold" />}
        onEmptySubmit={
          phase === "idle"
            ? () => {
                void start().catch((cause: unknown) => {
                  setError(
                    cause instanceof Error
                      ? cause.message
                      : t("Realtime connection failed"),
                  );
                });
              }
            : undefined
        }
        primaryAction={
          phase === "idle"
            ? undefined
            : {
                label: t("End realtime chat"),
                icon: <PhoneDisconnectIcon size={16} />,
                disabled: phase === "ending",
                onClick: stop,
              }
        }
        actionBeforeSubmit={
          phase === "ready" &&
          supportsInterruption && (
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label={t("Interrupt response")}
              title={t("Interrupt response")}
              onClick={() => interruptPlayback.current?.()}
            >
              <SpeakerSlashIcon size={16} />
            </Button>
          )
        }
      />
    ),
    status: (
      <>
        <span className="sr-only" role="status">
          {phase === "connecting" && t("Connecting…")}
          {phase === "ending" && t("Saving conversation…")}
        </span>
        {(error || (phase === "idle" && availability.reason)) && (
          <p role="status" className="text-sm text-muted-foreground">
            {error || availability.reason}
          </p>
        )}
      </>
    ),
  };
}

function waitForIce(
  peer: RTCPeerConnection,
  signal: AbortSignal,
  timeoutMessage: string,
): Promise<void> {
  signal.throwIfAborted();
  if (peer.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve, reject) => {
    const finish = (error?: Error) => {
      clearTimeout(timeout);
      peer.removeEventListener("icegatheringstatechange", changed);
      signal.removeEventListener("abort", aborted);
      if (error) reject(error);
      else resolve();
    };
    const changed = () => {
      if (peer.iceGatheringState === "complete") finish();
    };
    const aborted = () =>
      finish(new DOMException("WebRTC offer cancelled", "AbortError"));
    const timeout = setTimeout(() => finish(new Error(timeoutMessage)), 10_000);
    peer.addEventListener("icegatheringstatechange", changed);
    signal.addEventListener("abort", aborted, { once: true });
  });
}

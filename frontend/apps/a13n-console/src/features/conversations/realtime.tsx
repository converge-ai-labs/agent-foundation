import { WarningCircleIcon, WaveformIcon } from "@phosphor-icons/react";
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
import { VoiceAlpha, VoiceHud, type VoiceMedia } from "./realtime-hud";
import styles from "./realtime.module.css";
import captureModule from "./realtime-capture.js?url&no-inline";
import { invalidateConversation, runPath } from "./api";

interface Selection {
  agentId?: string;
  revisionId?: string | null;
  model?: string;
  threadId?: string;
  forkRunId?: string;
}

type Transport = "websocket" | "webrtc";

interface Availability {
  realtime: boolean;
  can_start: boolean;
  accepts_text: boolean;
  accepts_images?: boolean;
  supported_image_types?: readonly string[];
  max_images_per_input?: number;
  reason?: string | null;
  transports?: readonly Transport[];
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
          accepts_images: value.accepts_images,
          supported_image_types: value.supported_image_types,
          max_images_per_input: value.max_images_per_input,
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
  | { type: "sealed"; run_id: string; status: string; reason?: string }
  | { type: "draining"; code: "worker_draining" }
  | { type: "limit_warning"; remaining_seconds: number }
  | {
      type:
        | "barge_in"
        | "interrupted"
        | "playback_start"
        | "playback_end"
        | "input_discontinuity";
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
  | { type: "error"; message: string; code?: string; recoverable?: boolean };

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

type Draft = { text: string; assetIds: string[] };
type Retry = {
  forcePcm?: boolean;
  reconnected?: boolean;
  muted?: boolean;
  deviceId?: string;
  note?: string;
};

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
  const [note, setNote] = useState("");
  const [resume, setResume] = useState<() => void>();
  const [transport, setTransport] = useState<Transport>("websocket");
  const [startedAt, setStartedAt] = useState(0);
  const [limitAt, setLimitAt] = useState<number>();
  const [muted, setMuted] = useState(false);
  const [deviceId, setDeviceId] = useState("");
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [switchingMic, setSwitchingMic] = useState(false);
  const media = useRef<VoiceMedia | null>(null);
  const userStopped = useRef(false);
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
    options: CallOptions,
    draft?: Draft,
    displayedRunId?: string,
    retry: Retry = {},
  ): Promise<void | false> {
    const {
      agentId,
      revisionId,
      threadId,
      forkRunId,
      freshEnvironments,
      setup,
      availability,
      onAccepted,
    } = options;
    if (!agentId || !canRun || cleanup.current || !availability.can_start)
      throw new Error(t("Voice connection failed."));
    const useWebRTC =
      !retry.forcePcm &&
      availability.transports?.includes("webrtc") &&
      typeof RTCPeerConnection !== "undefined";
    let context: AudioContext;
    const abort = new AbortController();
    let settleFirst: ((result?: Error | false) => void) | undefined;
    const canFallback =
      useWebRTC && availability.transports?.includes("websocket");
    let input: MediaStream | undefined;
    let switchingInput: MediaStream | undefined;
    let micSource: MediaStreamAudioSourceNode | undefined;
    let sender: RTCRtpSender | undefined;
    let micMuted = retry.muted ?? false;
    let selectedDevice = retry.deviceId || "";
    let micLevel = 0;
    let switching = false;
    let accepted = false;
    let offered = false;
    let startupFailure = false;
    let endReason: string | undefined;
    let startSettings:
      Awaited<ReturnType<NonNullable<CallOptions["setup"]>>> | undefined;
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
    const acquireMic = (id: string) =>
      navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          ...(id ? { deviceId: { exact: id } } : {}),
        },
      });
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
      switchingInput?.getTracks().forEach((track) => track.stop());
      micSource?.disconnect?.();
      capture?.disconnect();
      if (speaker) {
        speaker.pause();
        speaker.srcObject = null;
      }
      void context?.close();
    };
    const dispose = (settle = true) => {
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
      media.current = null;
      if (peer) peer.onconnectionstatechange = null;
      peer?.close();
      if (connection && connection.readyState < WebSocket.CLOSING)
        connection.close();
      if (settle) settleFirst?.(false);
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
    const failPeer = () => {
      if (!ready) startupFailure = true;
      else endReason ??= "realtime_transport_failed";
      failMedia(t("Voice connection failed."));
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
        micSource = context.createMediaStreamSource(input!);
        micSource.connect(capture);
        capture.connect(context.destination);
        if (micMuted) capture.port.postMessage({ muted: true });
        if (!useWebRTC) {
          remoteLevel = context.createAnalyser?.();
          if (remoteLevel) {
            remoteLevel.fftSize = remoteSamples.length;
            remoteLevel.connect(context.destination);
          }
        }
        capture.port.onmessage = ({
          data,
        }: MessageEvent<ArrayBuffer | "speech" | { level: number }>) => {
          if (typeof data === "object" && "level" in data) {
            micLevel = micMuted ? 0 : data.level;
            return;
          }
          if (
            ready &&
            !micMuted &&
            !mediaStopped &&
            connection?.readyState === WebSocket.OPEN
          ) {
            if (data === "speech") {
              if (hasPlayback()) interrupt();
              return;
            }
            if (connection.bufferedAmount >= 64 * 1024) {
              failMedia(t("Voice connection failed."));
              return;
            }
            connection.send(data);
          }
        };
      } catch {
        failMedia(t("Voice connection failed."));
        return;
      }
      ready = true;
      clearTimeout(startupTimer);
      input?.getAudioTracks?.().forEach((track) => {
        track.enabled = !micMuted;
      });
      if (speaker) speaker.muted = false;
      setAcceptsText(serverReady.accepts_text);
      setSupportsInterruption(serverReady.supports_interruption);
      setPhase("ready");
      settleFirst?.();
    };
    interruptPlayback.current = interrupt;
    cleanup.current = dispose;
    userStopped.current = false;
    setError("");
    setNote(retry.note ?? "");
    setResume(undefined);
    setMuted(micMuted);
    setSwitchingMic(false);
    setDeviceId(selectedDevice);
    setDevices([]);
    setTransport(useWebRTC ? "webrtc" : "websocket");
    setStartedAt(Date.now());
    setLimitAt(undefined);
    setPhase("connecting");
    setOwner(displayedRunId ?? ownerId);
    dialingOwner.current = runRoute.current ? undefined : ownerId;
    if (displayedRunId) setActiveRun({ id: displayedRunId, availability });
    setApprovals([]);
    try {
      context = new AudioContext();
      await context.resume();
      if (disposed) return false;
      input = await acquireMic(selectedDevice);
      if (disposed) {
        input.getTracks().forEach((track) => track.stop());
        return false;
      }
      await context.audioWorklet.addModule(captureModule);
      if (disposed) return false;
      const refreshDevices = async () => {
        try {
          const listed = await navigator.mediaDevices.enumerateDevices?.();
          if (!mediaStopped && listed)
            setDevices(listed.filter((device) => device.kind === "audioinput"));
        } catch {
          // The active capture remains usable when the browser cannot list devices.
        }
      };
      void refreshDevices();
      navigator.mediaDevices.addEventListener?.(
        "devicechange",
        refreshDevices,
        { signal: abort.signal },
      );
      const armTrack = (track: MediaStreamTrack) => {
        track.enabled = ready && !micMuted;
        track.onended = () => failMedia(t("Voice connection failed."));
      };
      const track = input.getAudioTracks?.()[0];
      selectedDevice = track?.getSettings?.().deviceId || selectedDevice;
      setDeviceId(selectedDevice);
      if (track) armTrack(track);
      media.current = {
        levels: () => {
          let output = 0;
          if (remoteLevel && !mediaStopped && !speaker?.muted) {
            remoteLevel.getFloatTimeDomainData(remoteSamples);
            output = Math.sqrt(
              remoteSamples.reduce((sum, sample) => sum + sample * sample, 0) /
                remoteSamples.length,
            );
          }
          return [micMuted || mediaStopped ? 0 : micLevel, output];
        },
        toggleMute: () => {
          if (!ready || mediaStopped) return;
          micMuted = !micMuted;
          input?.getAudioTracks().forEach((track) => {
            track.enabled = !micMuted;
          });
          capture?.port.postMessage({ muted: micMuted });
          micLevel = 0;
          setMuted(micMuted);
        },
        switchMic: async (id) => {
          if (
            id === selectedDevice ||
            !ready ||
            mediaStopped ||
            switching ||
            !capture
          )
            return;
          switching = true;
          setSwitchingMic(true);
          let replacement: MediaStream | undefined;
          let source: MediaStreamAudioSourceNode | undefined;
          try {
            replacement = await acquireMic(id);
            if (mediaStopped) return;
            switchingInput = replacement;
            const track = replacement.getAudioTracks()[0];
            if (!track) throw new Error(t("Could not switch microphones."));
            track.enabled = false;
            source = context.createMediaStreamSource(replacement);
            await sender?.replaceTrack(track);
            if (mediaStopped) return;
            armTrack(track);
            source.connect(capture);
            micSource?.disconnect();
            micSource = source;
            input?.getTracks().forEach((old) => {
              old.onended = null;
              old.stop();
            });
            input = replacement;
            replacement = undefined;
            selectedDevice = track.getSettings?.().deviceId || id;
            setDeviceId(selectedDevice);
            setError("");
            void refreshDevices();
          } catch {
            if (!mediaStopped) setError(t("Could not switch microphones."));
          } finally {
            if (replacement) {
              source?.disconnect();
              replacement.getTracks().forEach((track) => track.stop());
            }
            switchingInput = undefined;
            switching = false;
            if (!mediaStopped) setSwitchingMic(false);
          }
        },
      };
      startSettings = await setup?.();
      const ticket = await client
        .workspace(workspace.id)
        .POST("/api/v1/realtime/sessions", {
          body: {
            ...startSettings,
            agent_id: agentId,
            agent_revision_id: revisionId,
            thread_id: threadId,
            fork_run_id: forkRunId,
            fresh_environments: freshEnvironments,
            audio: true,
            draft: draft?.text,
            draft_asset_ids: draft?.assetIds.length
              ? draft.assetIds
              : undefined,
            transport: useWebRTC ? "webrtc" : "websocket",
          },
          signal: abort.signal,
        })
        .then(data);
      if (disposed) return false;
      audioInputHz = ticket.audio_input_hz ?? audioInputHz;
      audioOutputHz = ticket.audio_output_hz ?? audioOutputHz;
      if (useWebRTC) {
        try {
          const rtc = new RTCPeerConnection({
            iceServers: ticket.ice_servers?.map(
              ({ urls, username, credential }) => ({
                urls,
                username: username ?? undefined,
                credential: credential ?? undefined,
              }),
            ),
          });
          peer = rtc;
          const playback = new Audio();
          speaker = playback;
          playback.autoplay = true;
          playback.muted = true;
          if (track) sender = rtc.addTrack(track, input);
          rtc.ontrack = (event) => {
            if (disposed || mediaStopped) return;
            const remote = event.streams[0] ?? new MediaStream([event.track]);
            playback.srcObject = remote;
            remoteLevel = context.createAnalyser();
            remoteLevel.fftSize = remoteSamples.length;
            context.createMediaStreamSource(remote).connect(remoteLevel);
            void playback
              .play()
              .catch(() => failMedia(t("Voice connection failed.")));
          };
          await rtc.setLocalDescription(await rtc.createOffer());
          await waitForIce(
            rtc,
            abort.signal,
            t("Audio network setup timed out."),
          );
          if (disposed) return false;
          offer = rtc.localDescription?.sdp;
          if (
            !offer ||
            rtc.connectionState === "failed" ||
            rtc.connectionState === "closed"
          )
            throw new Error(t("Voice connection failed."));
          rtc.onconnectionstatechange = () => {
            if (rtc.connectionState === "connected") startConversation();
            if (
              rtc.connectionState === "failed" ||
              rtc.connectionState === "closed"
            )
              failPeer();
          };
        } catch (cause) {
          startupFailure = true;
          throw cause;
        }
      }
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
      const firstMessage = draft
        ? new Promise<void | false>((resolve, reject) => {
            settleFirst = (result) =>
              result instanceof Error ? reject(result) : resolve(result);
          })
        : undefined;
      if (useWebRTC) startupTimer = setTimeout(failPeer, 30_000);
      connected.onopen = () => {
        if (offer) {
          connected.send(JSON.stringify({ type: "offer", sdp: offer }));
          offered = true;
        }
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
          playback.connect(remoteLevel ?? context.destination);
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
          endReason = event.reason ?? endReason;
          setApprovals([]);
          if (isTerminalVoiceReason(endReason))
            setError(reasonMessage(endReason));
        }
        if (event.type === "playback_position") nextOffset = event.offset;
        if (event.type === "barge_in" && !micMuted && hasPlayback())
          interrupt();
        if (event.type === "interrupted") interrupting = false;
        if (
          (event.type === "playback_start" || event.type === "playback_end") &&
          speaker &&
          !mediaStopped
        ) {
          speaker.muted = false;
        }
        if (event.type === "limit_warning")
          setLimitAt(Date.now() + event.remaining_seconds * 1000);
        if (event.type === "draining" || event.type === "ending") {
          endReason = event.type === "draining" ? event.code : event.reason;
          if (isTerminalVoiceReason(endReason))
            setError(reasonMessage(endReason));
          stopMedia.current?.();
          setPhase("ending");
        }
        if (event.type === "input_discontinuity")
          setError(t("Voice input was interrupted. Please repeat that part."));
        if (event.type === "accepted") {
          accepted = true;
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
            .catch(failPeer);
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
          const reason = event.code;
          setError(
            isTerminalVoiceReason(reason)
              ? reasonMessage(reason)
              : event.message,
          );
          if (!event.recoverable) endReason = reason ?? endReason;
        }
      };
      connected.onerror = () => setError(t("Voice connection failed."));
      let closing = false;
      const finish = async (code: number) => {
        if (disposed || closing) return;
        closing = true;
        stopMedia.current?.();
        clearTimeout(startupTimer);
        clearTimeout(endingTimer.current);
        setApprovals([]);
        setPhase("ending");
        if (!userStopped.current && !endReason && code === 1012 && accepted)
          endReason = "worker_draining";
        const fallback = () =>
          canFallback &&
          !ready &&
          ((startupFailure && !endReason) ||
            endReason === "realtime_transport_failed");
        const dropping = () =>
          !userStopped.current &&
          (endReason === "realtime_transport_failed" ||
            (!endReason && code !== 1000 && accepted));
        const deferUntilSeal =
          fallback() || dropping() || endReason === "worker_draining";
        if (
          (!ready || code !== 1000) &&
          !userStopped.current &&
          !deferUntilSeal
        )
          setError((value) => value || t("Voice connection failed."));
        let nextCall: (() => Promise<void | false>) | undefined;
        let auto = false;
        try {
          if (!receipt) return;
          let exists = accepted || sealed;
          if (!sealed && (!fallback() || offered)) {
            const api = client.workspace(workspace.id);
            const signal = AbortSignal.any([
              abort.signal,
              AbortSignal.timeout(20_000),
            ]);
            const retryDelays = [500, 1000, 2000, 3000, 4000];
            for (let attempt = 0; ; attempt++) {
              const result = await api.GET("/api/v1/runs/{run_id}", {
                params: { path: { run_id: receipt.run_id } },
                signal,
              });
              if (result.response.status !== 404) {
                const run = data(result);
                exists = true;
                if (run.sealed_at) {
                  sealed = true;
                  if (run.failure && !isTerminalVoiceReason(endReason)) {
                    endReason = run.failure.code;
                    if (
                      !fallback() &&
                      !dropping() &&
                      endReason !== "worker_draining"
                    )
                      setError(run.failure.message);
                  }
                }
                if (sealed || !deferUntilSeal) break;
              }
              const retryDelay = retryDelays[attempt];
              if (retryDelay === undefined) break;
              await delay(retryDelay, signal);
            }
          }
          if (disposed) return;
          if (exists) {
            void invalidateConversation(cache, workspace.id, {
              sessionId: receipt.session_id,
              threadId: receipt.thread_id,
              runId: receipt.run_id,
            });
            if (!accepted) {
              runRoute.current = runPath(basePath, receipt);
              setOwner(receipt.run_id);
              onAccepted(receipt);
            }
          }
          if (isTerminalVoiceReason(endReason)) {
            setError(reasonMessage(endReason));
            return;
          }
          const draining = endReason === "worker_draining";
          const pcm = fallback() && (!exists || sealed);
          if (
            !userStopped.current &&
            (pcm || (sealed && (draining || dropping())))
          ) {
            auto = pcm || (draining && !retry.reconnected);
            const resumed = exists ? receipt : undefined;
            const settings = startSettings;
            const follow = resumed
              ? {
                  ...options,
                  availability: { ...availability, can_start: true },
                  threadId: resumed.thread_id,
                  forkRunId: undefined,
                  freshEnvironments: undefined,
                  setup: async () =>
                    forkRunId ? {} : { options: settings?.options },
                }
              : { ...options, setup: async () => settings ?? {} };
            nextCall = () =>
              start(
                ownerId,
                follow,
                resumed ? undefined : draft,
                resumed?.run_id ?? displayedRunId,
                {
                  forcePcm: pcm || retry.forcePcm,
                  reconnected: (auto && draining) || retry.reconnected,
                  muted: micMuted,
                  deviceId: selectedDevice,
                  note: pcm
                    ? t("WebRTC could not connect. Switching to PCM audio.")
                    : draining && auto
                      ? t("Reconnecting after a server update…")
                      : undefined,
                },
              );
            if (!auto)
              setError(
                t(
                  "Voice connection dropped. You can resume in this conversation.",
                ),
              );
          } else if (deferUntilSeal && !sealed && !userStopped.current) {
            setError(
              t(
                "Voice connection ended. Wait for the conversation to finish saving before starting another call.",
              ),
            );
          }
        } catch {
          if (!disposed) setError(t("Voice connection failed."));
        } finally {
          if (!disposed) {
            void cache.invalidateQueries({
              queryKey: ["realtime", workspace.id, agentId],
            });
            const previousRoute = runRoute.current;
            if (auto && nextCall) {
              dispose(false);
              runRoute.current = previousRoute;
              try {
                const result = await nextCall();
                settleFirst?.(
                  result === false || (accepted && !serverReady)
                    ? false
                    : undefined,
                );
              } catch (cause) {
                setError(
                  cause instanceof Error
                    ? cause.message
                    : t("Voice connection failed."),
                );
                settleFirst?.(false);
              }
            } else {
              dispose();
              if (nextCall)
                setResume(() => () => {
                  runRoute.current = previousRoute;
                  void nextCall?.().catch((cause: unknown) => {
                    setError(
                      cause instanceof Error
                        ? cause.message
                        : t("Voice connection failed."),
                    );
                  });
                });
            }
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
      const previousRoute = runRoute.current;
      dispose();
      if (canFallback && startupFailure) {
        runRoute.current = previousRoute;
        return start(
          ownerId,
          { ...options, setup: async () => startSettings ?? {} },
          draft,
          displayedRunId,
          {
            ...retry,
            forcePcm: true,
            muted: micMuted,
            deviceId: selectedDevice,
            note: t("WebRTC could not connect. Switching to PCM audio."),
          },
        );
      }
      throw cause;
    }
  }

  function reasonMessage(reason: string): string {
    switch (reason) {
      case "duration_limit":
        return t("Voice call reached its time limit.");
      case "idle_timeout":
        return t("Voice call ended after being idle.");
      case "usage_limit_exceeded":
        return t("Voice call reached its usage limit.");
      case "authority_revoked":
        return t("You no longer have permission to continue this call.");
      default:
        return t("Voice connection failed.");
    }
  }

  function sendInput({ text, assetIds }: Draft) {
    if (phase !== "ready" || socket.current?.readyState !== WebSocket.OPEN)
      throw new Error(t("Voice connection failed."));
    setError("");
    socket.current.send(
      JSON.stringify(
        assetIds.length
          ? { type: "input", text, asset_ids: assetIds, respond: true }
          : { type: "text", text },
      ),
    );
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
    userStopped.current = true;
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
    note,
    resume,
    transport,
    startedAt,
    limitAt,
    muted,
    deviceId,
    devices,
    switchingMic,
    media,
    setError,
    approvals,
    acceptsText,
    supportsInterruption,
    start,
    sendInput,
    stop,
    decide,
    interruptPlayback,
    dialingOwner,
    cleanup,
  };
}

function isTerminalVoiceReason(reason?: string): reason is string {
  return (
    reason === "duration_limit" ||
    reason === "idle_timeout" ||
    reason === "usage_limit_exceeded" ||
    reason === "authority_revoked"
  );
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
      {controls?.status}
      {controls?.editor ?? children}
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
    sendInput,
    supportsInterruption,
    interruptPlayback,
    stop,
    decide,
    setError,
  } = call;
  const mine = owner === id || (!!displayedRunId && owner === displayedRunId);
  const phase = mine ? call.phase : "idle";
  const error = mine ? call.error : "";
  const resume = mine && phase === "idle" ? call.resume : undefined;
  const approvals = mine ? call.approvals : [];
  const acceptsText =
    phase === "ready" ? call.acceptsText : availability.accepts_text;
  const imageLimit = availability.accepts_images
    ? (availability.max_images_per_input ?? 0)
    : 0;
  const start = (draft?: Draft) => call.start(id, voice, draft, displayedRunId);
  async function submit(payload: Schema["MessagePayload"]) {
    const text = payload.content
      .flatMap((part) => (part.type === "text" ? [part.text.trim()] : []))
      .join("\n\n");
    const assetIds = payload.content.flatMap((part) =>
      part.type === "asset" ? [part.asset_id] : [],
    );
    if (!text && !assetIds.length)
      throw new Error(t("Write a message or attach content first."));
    if (text.length > 8192)
      throw new Error(t("Messages must be between 1 and 8,192 characters."));
    if (assetIds.length > imageLimit)
      throw new Error(
        t("Image limit per message: {{limit}}.", { limit: imageLimit }),
      );
    const draft = { text, assetIds };
    if (phase === "idle") return await start(draft);
    sendInput(draft);
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
        options={
          phase === "idle" ? (
            <>
              {controls}
              <VoiceAlpha />
            </>
          ) : undefined
        }
        agentName={agentName}
        initial={initial}
        textInput={acceptsText}
        attachmentsEnabled={
          imageLimit > 0 && (phase === "idle" || phase === "ready")
        }
        imageTypes={availability.supported_image_types ?? []}
        imageLimit={imageLimit}
        submitDisabled={
          (phase === "idle" &&
            (disabled || !availability.can_start || call.phase !== "idle")) ||
          phase === "connecting" ||
          phase === "ending"
        }
        submit={submit}
        label={phase === "ready" ? t("Send message") : t("Start voice chat")}
        submitIcon={<WaveformIcon size={17} weight="bold" />}
        onEmptySubmit={
          phase === "idle"
            ? () => {
                void start().catch((cause: unknown) => {
                  setError(
                    cause instanceof Error
                      ? cause.message
                      : t("Voice connection failed."),
                  );
                });
              }
            : undefined
        }
      />
    ),
    status: (
      <>
        {phase !== "idle" && (
          <VoiceHud
            phase={phase}
            transport={call.transport}
            startedAt={call.startedAt}
            limitAt={call.limitAt}
            muted={call.muted}
            devices={call.devices}
            deviceId={call.deviceId}
            switching={call.switchingMic}
            media={call.media}
            stop={stop}
            interrupt={
              supportsInterruption
                ? () => interruptPlayback.current?.()
                : undefined
            }
          />
        )}
        {mine && phase !== "idle" && call.note && (
          <p role="status" className={styles.note}>
            {call.note}
          </p>
        )}
        {error || resume ? (
          <div className={resume ? styles.recovery : undefined}>
            {error && (
              <p role="alert" className={styles.error}>
                <WarningCircleIcon size={14} aria-hidden="true" />
                {error}
              </p>
            )}
            {resume && (
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={resume}
                disabled={!!disabled}
              >
                <WaveformIcon size={14} aria-hidden="true" />
                {t("Resume voice call")}
              </Button>
            )}
          </div>
        ) : phase === "idle" && availability.reason ? (
          <p role="status" className={styles.note}>
            {availability.reason}
          </p>
        ) : null}
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

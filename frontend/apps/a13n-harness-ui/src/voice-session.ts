import { result, type Schema, type Transport } from "./transport/client";

export type VoiceState = {
  status: "idle" | "starting" | "connecting" | "active" | "failed";
  error: string;
  snapshot: Schema<"VoiceSnapshot"> | null;
};
type Call = {
  owner: string;
  id: string;
  abort: AbortController;
  socket: WebSocket | null;
  peer: RTCPeerConnection | null;
  stream: MediaStream | null;
  audio: HTMLAudioElement;
  answer: boolean;
  timeout: ReturnType<typeof setTimeout> | null;
};

/** One tab lifetime, independent of navigation. Every new call needs a user action. */
export class VoiceSession {
  private call: Call | null = null;
  private listeners = new Set<() => void>();
  private state: VoiceState = { status: "idle", error: "", snapshot: null };
  constructor(private transport: Transport) {}
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  getSnapshot = () => this.state;
  private update(patch: Partial<VoiceState>) {
    this.state = { ...this.state, ...patch };
    for (const listener of this.listeners) listener();
  }
  private release(call: Call) {
    if (this.call === call) this.call = null;
    if (call.timeout !== null) clearTimeout(call.timeout);
    call.abort.abort();
    call.stream?.getTracks().forEach((track) => track.stop());
    call.peer?.close();
    call.socket?.close();
    call.audio.pause();
    call.audio.srcObject = null;
  }
  stop = () => {
    const call = this.call;
    if (call) this.release(call); // Control disconnect stops Host voice, never executor Runs.
    this.update({ status: "idle", error: "" });
  };
  private fail(call: Call, error: unknown) {
    if (this.call !== call) return;
    this.release(call);
    this.update({
      status: "failed",
      error:
        error instanceof Error
          ? error.message
          : "Voice disconnected. Start again when ready.",
    });
  }
  start = async () => {
    if (this.call) return;
    const call: Call = {
      owner: crypto.randomUUID(),
      id: crypto.randomUUID(),
      abort: new AbortController(),
      socket: null,
      peer: null,
      stream: null,
      audio: new Audio(),
      answer: false,
      timeout: null,
    };
    this.call = call;
    this.update({ status: "starting", error: "", snapshot: null });
    call.timeout = setTimeout(
      () =>
        this.fail(
          call,
          new Error("Voice connection timed out. Start again when ready."),
        ),
      60_000,
    );
    try {
      if (
        !navigator.mediaDevices?.getUserMedia ||
        !globalThis.RTCPeerConnection
      )
        throw new Error(
          "Voice needs a browser with microphone access on HTTPS or localhost.",
        );
      // Claim controls before asking for microphone permission. No automatic reconnect.
      await new Promise<void>((resolve, reject) => {
        const url = new URL("/api/voice/connect", window.location.origin);
        url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
        const socket = new WebSocket(url);
        call.socket = socket;
        const aborted = () =>
          reject(new DOMException("Voice stopped", "AbortError"));
        call.abort.signal.addEventListener("abort", aborted, { once: true });
        let ready = false;
        socket.onopen = () => {
          if (this.call !== call) return;
          socket.send(JSON.stringify({ api_key: this.transport.key }));
          socket.send(JSON.stringify({ owner_id: call.owner }));
        };
        socket.onmessage = (event) => {
          if (this.call !== call) return;
          try {
            const frame = JSON.parse(event.data) as Schema<"VoiceSnapshot"> & {
              kind?: string;
            };
            if (frame.kind === "ping") {
              socket.send(JSON.stringify({ kind: "pong" }));
              return;
            }
            if (!ready) {
              ready = true;
              resolve();
              return;
            }
            if (frame.start_id !== call.id) return;
            this.update({ snapshot: frame, error: frame.error ?? "" });
            if (frame.status === "failed" || frame.status === "idle") {
              this.fail(
                call,
                new Error(
                  frame.error || "Voice session ended. Start again when ready.",
                ),
              );
            } else if (frame.answer_sdp && call.peer && !call.answer) {
              call.answer = true;
              this.update({ status: "connecting" });
              void call.peer
                .setRemoteDescription({ type: "answer", sdp: frame.answer_sdp })
                .catch((error) => this.fail(call, error));
            } else if (frame.status === "active") {
              if (call.timeout !== null) clearTimeout(call.timeout);
              this.update({ status: "active" });
            }
          } catch (error) {
            this.fail(call, error);
          }
        };
        socket.onerror = () => {
          const error = new Error("Could not connect Live controls.");
          reject(error);
          this.fail(call, error);
        };
        socket.onclose = (event) => {
          const error = new Error(
            event.reason ||
              "Live controls disconnected. Start again when ready.",
          );
          reject(error);
          this.fail(call, error);
        };
      });
      call.abort.signal.throwIfAborted();
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
        video: false,
      });
      if (this.call !== call) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      call.stream = stream;
      const peer = new RTCPeerConnection();
      call.peer = peer;
      stream.getTracks().forEach((track) => {
        peer.addTrack(track, stream);
        track.onended = () =>
          this.fail(call, new Error("Microphone access ended."));
      });
      peer.createDataChannel("oai-events");
      peer.ontrack = (event) => {
        if (this.call !== call) return;
        call.audio.srcObject =
          event.streams[0] ?? new MediaStream([event.track]);
        void call.audio
          .play()
          .catch(() =>
            this.fail(
              call,
              new Error(
                "Audio playback was blocked. Allow sound and start again.",
              ),
            ),
          );
      };
      peer.onconnectionstatechange = () => {
        if (this.call !== call) return;
        if (peer.connectionState === "connected") {
          call.socket?.send(
            JSON.stringify({ kind: "media_connected", start_id: call.id }),
          );
        } else if (
          ["failed", "disconnected", "closed"].includes(peer.connectionState)
        ) {
          this.fail(
            call,
            new Error("Audio disconnected. Start a new voice session."),
          );
        }
      };
      await peer.setLocalDescription(await peer.createOffer());
      await waitForIce(peer, call.abort.signal);
      call.abort.signal.throwIfAborted();
      await result(
        this.transport.client.POST("/api/voice/start", {
          body: {
            owner_id: call.owner,
            start_id: call.id,
            offer_sdp: peer.localDescription!.sdp,
          },
          signal: call.abort.signal,
        }),
      );
    } catch (error) {
      this.fail(call, error);
    }
  };
}

function waitForIce(
  peer: RTCPeerConnection,
  signal: AbortSignal,
): Promise<void> {
  if (peer.iceGatheringState === "complete") return Promise.resolve();
  return new Promise((resolve, reject) => {
    const cleanup = () => {
      clearTimeout(timeout);
      peer.removeEventListener("icegatheringstatechange", changed);
      signal.removeEventListener("abort", aborted);
    };
    const changed = () => {
      if (peer.iceGatheringState === "complete") {
        cleanup();
        resolve();
      }
    };
    const aborted = () => {
      cleanup();
      reject(new DOMException("Voice stopped", "AbortError"));
    };
    const timeout = setTimeout(() => {
      cleanup();
      reject(new Error("Audio network setup timed out."));
    }, 10_000);
    peer.addEventListener("icegatheringstatechange", changed);
    signal.addEventListener("abort", aborted, { once: true });
    if (signal.aborted) aborted();
  });
}

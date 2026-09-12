import type { Schema } from "../transport/client";

export const OUTPUT_LIMIT = 1024 * 1024;
type Frame = Schema<"TerminalFrame">;
export type TerminalState = {
  connection: "Detached" | "Connecting" | "Live";
  frame: Frame | null;
  pendingControl: boolean;
  message: string;
};
export type TerminalSink = {
  write: (text: string, done: () => void) => void;
  reset: () => void;
};

/** One selected view, not a session store. Only parsed output advances the cursor. */
export class TerminalConnection {
  state: TerminalState = {
    connection: "Detached",
    frame: null,
    pendingControl: false,
    message: "",
  };
  cursor = 0;
  private socket: WebSocket | null = null;
  private decoder = new TextDecoder();
  private queue: { bytes: Uint8Array; end: number; gap: boolean }[] = [];
  private pendingBytes = 0;
  private writing = false;
  private disposed = false;
  private flushTimer: ReturnType<typeof setTimeout> | undefined;
  private controlTimer: ReturnType<typeof setTimeout> | undefined;
  private received = 0;

  constructor(
    readonly id: string,
    private readonly sink: TerminalSink,
    private readonly changed: (state: TerminalState) => void,
    private readonly unauthorized: () => void,
  ) {}
  get controls() {
    const { frame, connection, pendingControl } = this.state;
    return (
      connection === "Live" &&
      !pendingControl &&
      !!frame &&
      frame.terminal.state === "running" &&
      frame.terminal.controller === frame.participant_id
    );
  }
  private publish(patch: Partial<TerminalState>) {
    this.state = { ...this.state, ...patch };
    if (!this.disposed) this.changed(this.state);
  }
  connect(origin: string, key: string) {
    if (this.disposed || this.socket || this.writing || this.queue.length)
      return;
    const url = new URL(
      `/api/host/terminals/${encodeURIComponent(this.id)}/connect`,
      origin,
    );
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    url.searchParams.set("cursor", String(this.cursor));
    this.received = this.cursor;
    this.publish({
      connection: "Connecting",
      frame: null,
      pendingControl: false,
      message: "",
    });
    const socket = new WebSocket(url);
    this.socket = socket;
    socket.onopen = () =>
      socket.send(
        JSON.stringify({
          api_key: key,
        } satisfies Schema<"InteractiveAuthentication">),
      );
    socket.onmessage = (event) => {
      if (this.socket !== socket) return;
      try {
        const frame = JSON.parse(String(event.data)) as
          Frame | Schema<"ErrorEnvelope">;
        if ("error" in frame) {
          this.detach(
            `${frame.error.message} Reattach to inspect current authority; input is never replayed.`,
          );
          return;
        }
        if (
          frame.kind !== "terminal" ||
          frame.terminal.terminal_id !== this.id ||
          !Number.isSafeInteger(frame.start) ||
          !Number.isSafeInteger(frame.end) ||
          frame.start < 0 ||
          frame.end < frame.start ||
          frame.end - frame.start > OUTPUT_LIMIT
        )
          throw new Error("Invalid terminal frame.");
        const bytes = Uint8Array.from(atob(frame.data_base64), (char) =>
          char.charCodeAt(0),
        );
        if (
          bytes.length !== frame.end - frame.start ||
          (!frame.gap && frame.start !== this.received)
        )
          throw new Error("Terminal output positions are not contiguous.");
        if (this.pendingBytes + bytes.length > OUTPUT_LIMIT) {
          this.detach(
            "Output paused: this view could not keep up. Reattach after rendering; only retained bytes can be recovered.",
          );
          return;
        }
        this.received = frame.end;
        const confirmed =
          this.state.frame &&
          frame.terminal.control_epoch !==
            this.state.frame.terminal.control_epoch;
        if (confirmed) clearTimeout(this.controlTimer);
        this.publish({
          connection: "Live",
          frame,
          pendingControl: confirmed ? false : this.state.pendingControl,
          ...(confirmed
            ? { message: "Control state confirmed by the server." }
            : {}),
          ...(frame.gap
            ? {
                message:
                  "Output gap: missing bytes are unavailable. The screen restarts from retained raw output, not a full screen snapshot.",
              }
            : {}),
        });
        if (bytes.length || frame.gap) {
          this.queue.push({ bytes, end: frame.end, gap: frame.gap });
          this.pendingBytes += bytes.length;
          this.schedule();
        }
      } catch {
        this.detach(
          "Invalid terminal output. Reattach to inspect; no input has been retried.",
        );
      }
    };
    socket.onclose = (event) => {
      if (this.socket !== socket) return;
      this.detach(
        "Disconnected. The shared process may still be running. Reattach as a read-only viewer.",
      );
      if (event.code === 4401) this.unauthorized();
    };
  }
  private schedule() {
    if (this.writing || this.flushTimer || this.disposed) return;
    this.flushTimer = setTimeout(() => {
      this.flushTimer = undefined;
      this.flush();
    }, 16);
  }
  private flush() {
    if (this.disposed || this.writing || !this.queue.length) return;
    // Batch adjacent frames, but never join across a disclosed byte gap.
    const batch = [this.queue.shift()!];
    let size = batch[0].bytes.length;
    while (this.queue.length && !this.queue[0].gap && size < 64 * 1024) {
      const next = this.queue.shift()!;
      batch.push(next);
      size += next.bytes.length;
    }
    if (batch[0].gap) {
      this.decoder = new TextDecoder();
      this.sink.reset();
    }
    const text = batch
      .map((part) => this.decoder.decode(part.bytes, { stream: true }))
      .join("");
    this.writing = true;
    this.sink.write(text, () => {
      if (this.disposed) return;
      this.cursor = batch[batch.length - 1].end;
      this.pendingBytes -= size;
      this.writing = false;
      this.publish({});
      this.schedule();
    });
  }
  get draining() {
    return this.writing || this.queue.length > 0;
  }
  private send(command: Schema<"TerminalCommand">) {
    if (this.socket?.readyState !== WebSocket.OPEN) return false;
    if (this.socket.bufferedAmount > 64 * 1024) {
      this.detach(
        "Input connection is congested. Delivery may be partial; inspect before typing again. Nothing is replayed.",
      );
      return false;
    }
    this.socket.send(JSON.stringify(command));
    return true;
  }
  control(release = false) {
    const { frame, connection, pendingControl } = this.state;
    if (
      !frame ||
      connection !== "Live" ||
      pendingControl ||
      frame.terminal.state !== "running"
    )
      return;
    this.publish({
      pendingControl: true,
      message: "Waiting for server control confirmation…",
    });
    if (
      !this.send({
        kind: "control",
        control_epoch: frame.terminal.control_epoch,
        release,
      })
    ) {
      this.detach("Control was not confirmed. Reattach to inspect.");
      return;
    }
    this.controlTimer = setTimeout(
      () =>
        this.detach(
          "Control acknowledgement is uncertain. Reattach as a viewer; no control action is retried.",
        ),
      10000,
    );
  }
  input(text: string) {
    if (!this.controls) return false;
    // The existing protocol is bounded UTF-8 text, not arbitrary binary input.
    if (new TextEncoder().encode(text).length > 16 * 1024) {
      this.publish({
        message:
          "Input exceeds 16 KiB. Paste a smaller selection; nothing from this paste was sent.",
      });
      return false;
    }
    return this.send({
      kind: "input",
      text,
      control_epoch: this.state.frame!.terminal.control_epoch,
    });
  }
  resize(rows: number, columns: number) {
    if (!this.controls) return false;
    const frame = this.state.frame!;
    if (frame.terminal.rows === rows && frame.terminal.columns === columns)
      return true;
    return this.send({
      kind: "resize",
      rows,
      columns,
      control_epoch: frame.terminal.control_epoch,
    });
  }
  detach(
    message = "Detached from this view. The shared process was not closed.",
  ) {
    const socket = this.socket;
    this.socket = null;
    socket?.close();
    clearTimeout(this.controlTimer);
    this.publish({ connection: "Detached", pendingControl: false, message });
  }
  dispose() {
    this.disposed = true;
    this.detach();
    clearTimeout(this.flushTimer);
    this.queue = [];
  }
}

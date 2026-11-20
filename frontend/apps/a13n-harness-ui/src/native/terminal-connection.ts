import type { Schema } from "../transport/client";

export const OUTPUT_LIMIT = 1024 * 1024;
export const BATCH_LIMIT = 256 * 1024;
const FLUSH_DELAY_MS = 8;
const INPUT_BATCH_DELAY_MS = 8;
const INPUT_LIMIT = 16 * 1024;
const SOCKET_BUFFER_LIMIT = 64 * 1024;

type Frame = Schema<"TerminalFrame">;
export type TerminalState = {
  connection: "Detached" | "Connecting" | "Live";
  frame: Frame | null;
  pendingControl: boolean;
  message: string;
  serverMessage?: string;
};
export type TerminalSink = {
  write: (text: string, done: () => void) => void;
  reset: () => void;
};

function decodeBase64(value: string): Uint8Array {
  if (!value) return new Uint8Array(0);
  const binary = atob(value);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

function sameTerminalView(
  a: Schema<"TerminalView"> | undefined,
  b: Schema<"TerminalView"> | undefined,
): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return (
    a.terminal_id === b.terminal_id &&
    a.state === b.state &&
    a.exit_code === b.exit_code &&
    a.controller === b.controller &&
    a.control_epoch === b.control_epoch &&
    a.rows === b.rows &&
    a.columns === b.columns &&
    a.cwd === b.cwd &&
    a.project_id === b.project_id &&
    a.participants.length === b.participants.length &&
    a.participants.every(
      (participant, index) => participant === b.participants[index],
    )
  );
}

const asciiDecoder = new TextDecoder("latin1");

function hasIncompleteRedraw(
  queue: { bytes: Uint8Array; gap: boolean }[],
): boolean {
  if (!queue.length || queue[0].gap) return false;
  let size = 0;
  const parts: string[] = [];
  for (let i = 0; i < queue.length; i++) {
    if (i > 0 && queue[i].gap) break;
    if (size + queue[i].bytes.length > BATCH_LIMIT && i > 0) break;
    parts.push(asciiDecoder.decode(queue[i].bytes));
    size += queue[i].bytes.length;
  }
  if (!size || size >= BATCH_LIMIT) return false;
  const raw = parts.join("");
  if (raw.lastIndexOf("\x1b[?2026h") > raw.lastIndexOf("\x1b[?2026l"))
    return true;
  if (raw.lastIndexOf("\x1b[?25l") > raw.lastIndexOf("\x1b[?25h")) return true;
  const tail = raw.slice(-16);
  const esc = tail.lastIndexOf("\x1b");
  if (esc >= 0) {
    const seq = tail.slice(esc);
    if (seq.length === 1) return true;
    const second = seq.charCodeAt(1);
    if (second === 91) {
      let complete = false;
      for (let i = 2; i < seq.length; i++) {
        const code = seq.charCodeAt(i);
        if (code >= 0x40 && code <= 0x7e) {
          complete = true;
          break;
        }
      }
      if (!complete) return true;
    } else if (second === 93) {
      if (!seq.includes("\x07") && !seq.includes("\x1b\\")) return true;
    }
  }
  return /\x1b\[(?:[023]?J|[02]?K|;?[1;]*H)$/.test(tail);
}

/** One selected view, not a session store. Only parsed output advances the cursor. */
export class TerminalConnection {
  state: TerminalState = {
    connection: "Detached",
    frame: null,
    pendingControl: false,
    message: "",
  };
  cursor = 0;
  recoverable = false;
  reconnectAttempts = 0;
  private restoreControl = false;
  private socket: WebSocket | null = null;
  private decoder = new TextDecoder();
  private queue: { bytes: Uint8Array; end: number; gap: boolean }[] = [];
  private pendingBytes = 0;
  private writing = false;
  private deferredFlush = false;
  private disposed = false;
  private flushTimer: ReturnType<typeof setTimeout> | undefined;
  private controlTimer: ReturnType<typeof setTimeout> | undefined;
  private inputTimer: ReturnType<typeof setTimeout> | undefined;
  private pendingInput = "";
  private pendingInputEpoch = -1;
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
  private publish(patch: Partial<TerminalState>, forceNotify = false) {
    const previous = this.state;
    const next: TerminalState = {
      ...previous,
      ...(patch.message !== undefined ? { serverMessage: undefined } : {}),
      ...patch,
    };
    this.state = next;
    if (this.disposed) return;
    const changed =
      forceNotify ||
      previous.connection !== next.connection ||
      previous.pendingControl !== next.pendingControl ||
      previous.message !== next.message ||
      previous.serverMessage !== next.serverMessage ||
      previous.frame?.participant_id !== next.frame?.participant_id ||
      !sameTerminalView(previous.frame?.terminal, next.frame?.terminal);
    if (changed) this.changed(this.state);
  }
  private clearPendingInput() {
    clearTimeout(this.inputTimer);
    this.inputTimer = undefined;
    this.pendingInput = "";
    this.pendingInputEpoch = -1;
  }
  connect(origin: string, key: string, claimCreated?: () => boolean) {
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
            "Reconnect to inspect current authority; input is never replayed.",
            frame.error.message,
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
        const bytes = decodeBase64(frame.data_base64);
        if (
          bytes.length !== frame.end - frame.start ||
          (!frame.gap && frame.start !== this.received)
        )
          throw new Error("Terminal output positions are not contiguous.");
        if (this.pendingBytes + bytes.length > OUTPUT_LIMIT) {
          this.closeSocket(
            "Output paused because this view fell behind. Reconnect to recover saved output; some may be missing.",
            undefined,
            true,
          );
          return;
        }
        this.received = frame.end;
        const firstFrame = !this.state.frame;
        const confirmed =
          this.state.frame &&
          frame.terminal.control_epoch !==
            this.state.frame.terminal.control_epoch;
        if (confirmed) {
          clearTimeout(this.controlTimer);
          this.clearPendingInput();
        }
        if (
          frame.terminal.controller !== frame.participant_id ||
          frame.terminal.state !== "running"
        ) {
          if (!firstFrame) this.restoreControl = false;
          this.clearPendingInput();
        }
        this.publish({
          connection: "Live",
          frame,
          pendingControl: confirmed ? false : this.state.pendingControl,
          ...(confirmed ? { message: "" } : {}),
          ...(frame.gap
            ? {
                message:
                  "Some output is missing. The terminal will rebuild from saved output, so the screen may be incomplete.",
              }
            : {}),
        });
        if (firstFrame) {
          this.recoverable = false;
          this.reconnectAttempts = 0;
          const created = claimCreated?.() ?? false;
          const reclaim = this.restoreControl;
          this.restoreControl = false;
          // Creation and view/transport resumption request control once after
          // authentication, and never take input away from a controller who won first.
          if (
            (created || reclaim) &&
            !frame.terminal.controller &&
            frame.terminal.state === "running"
          ) {
            this.control();
          }
        }
        if (bytes.length || frame.gap) {
          this.queue.push({ bytes, end: frame.end, gap: frame.gap });
          this.pendingBytes += bytes.length;
          this.schedule();
        }
      } catch {
        this.detach(
          "Invalid terminal output. Reconnect to check. No input was resent.",
        );
      }
    };
    socket.onclose = (event) => {
      if (this.socket !== socket) return;
      const unauthorized = event.code === 4401;
      const closed = this.state.frame?.terminal.state === "closed";
      if (!unauthorized && !closed) this.reconnectAttempts += 1;
      this.closeSocket(
        "Disconnected. The terminal may still be running. Reconnect to view it without taking control.",
        undefined,
        !unauthorized && !closed,
      );
      if (unauthorized) this.unauthorized();
    };
  }
  private schedule() {
    if (this.writing || this.flushTimer || this.disposed) return;
    this.flushTimer = setTimeout(() => {
      this.flushTimer = undefined;
      if (
        !this.deferredFlush &&
        this.queue.length &&
        hasIncompleteRedraw(this.queue)
      ) {
        this.deferredFlush = true;
        this.schedule();
        return;
      }
      this.deferredFlush = false;
      this.flush();
    }, FLUSH_DELAY_MS);
  }
  private flush() {
    if (this.disposed || this.writing || !this.queue.length) return;
    this.deferredFlush = false;
    // Batch adjacent frames, but never join across a disclosed byte gap.
    const batch = [this.queue.shift()!];
    let size = batch[0].bytes.length;
    while (this.queue.length && !this.queue[0].gap && size < BATCH_LIMIT) {
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
      if (this.queue.length) {
        if (hasIncompleteRedraw(this.queue)) this.schedule();
        else this.flush();
        return;
      }
      if (this.state.connection === "Detached") {
        this.publish({}, true);
      }
    });
  }
  get draining() {
    return this.writing || this.queue.length > 0;
  }
  private send(command: Schema<"TerminalCommand">) {
    if (this.socket?.readyState !== WebSocket.OPEN) return false;
    if (this.socket.bufferedAmount > SOCKET_BUFFER_LIMIT) {
      this.detach(
        "The connection is backed up. Some input may have arrived. Check the terminal before typing again. Nothing is resent.",
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
    if (release) this.restoreControl = false;
    this.clearPendingInput();
    this.publish({
      pendingControl: true,
    });
    if (
      !this.send({
        kind: "control",
        control_epoch: frame.terminal.control_epoch,
        release,
      })
    ) {
      this.detach("Control was not confirmed. Reconnect to inspect.");
      return;
    }
    this.controlTimer = setTimeout(
      () =>
        this.detach(
          "Couldn't confirm the control action. Reconnect as a viewer. Nothing was retried.",
        ),
      10000,
    );
  }
  private flushInput() {
    this.inputTimer = undefined;
    if (!this.pendingInput || !this.controls) {
      this.clearPendingInput();
      return;
    }
    const epoch = this.state.frame!.terminal.control_epoch;
    if (this.pendingInputEpoch !== epoch) {
      this.clearPendingInput();
      return;
    }
    const text = this.pendingInput;
    this.pendingInput = "";
    this.pendingInputEpoch = -1;
    this.send({
      kind: "input",
      text,
      control_epoch: epoch,
    });
  }
  input(text: string) {
    if (!this.controls) return false;
    const encodedLength = new TextEncoder().encode(text).length;
    // The existing protocol is bounded UTF-8 text, not arbitrary binary input.
    if (encodedLength > INPUT_LIMIT) {
      this.publish({
        message:
          "Paste is over the 16 KiB limit. Nothing was sent. Try a smaller selection.",
      });
      return false;
    }
    const epoch = this.state.frame!.terminal.control_epoch;
    if (
      this.socket &&
      this.socket.bufferedAmount > 0 &&
      this.socket.bufferedAmount <= SOCKET_BUFFER_LIMIT
    ) {
      const combined = this.pendingInput + text;
      if (new TextEncoder().encode(combined).length <= INPUT_LIMIT) {
        this.pendingInput = combined;
        this.pendingInputEpoch = epoch;
        if (!this.inputTimer) {
          this.inputTimer = setTimeout(
            () => this.flushInput(),
            INPUT_BATCH_DELAY_MS,
          );
        }
        return true;
      }
      clearTimeout(this.inputTimer);
      this.flushInput();
    } else if (this.pendingInput) {
      clearTimeout(this.inputTimer);
      this.flushInput();
    }
    return this.send({
      kind: "input",
      text,
      control_epoch: epoch,
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
  private closeSocket(
    message: string,
    serverMessage: string | undefined,
    recoverable: boolean,
  ) {
    if (recoverable && this.controls) {
      this.restoreControl = true;
    } else if (!recoverable) {
      this.restoreControl = false;
    }
    this.recoverable = recoverable;
    this.clearPendingInput();
    const socket = this.socket;
    this.socket = null;
    socket?.close();
    clearTimeout(this.controlTimer);
    this.publish({
      connection: "Detached",
      pendingControl: false,
      message,
      serverMessage,
    });
  }
  suspend() {
    this.closeSocket(
      "Detached from this view. The shared process was not closed.",
      undefined,
      true,
    );
  }
  detach(
    message = "Detached from this view. The shared process was not closed.",
    serverMessage?: string,
  ) {
    this.reconnectAttempts = 0;
    this.closeSocket(message, serverMessage, false);
  }
  dispose() {
    this.disposed = true;
    this.detach();
    clearTimeout(this.flushTimer);
    this.clearPendingInput();
    this.queue = [];
  }
}

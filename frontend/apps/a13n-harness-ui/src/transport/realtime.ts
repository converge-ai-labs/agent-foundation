import type { Schema } from "./client";

type Subscription = {
  stream: "summary" | "focus";
  root?: string;
  cursor: () => string | undefined;
  receive: (frame: unknown) => void;
  state: (value: string) => void;
};
type Channel = Subscription & { id: string };

// One authenticated socket per access lifetime. Channels retain their own
// cursors and can re-bootstrap without disturbing other observers or Runs.
export class Realtime {
  private socket?: WebSocket;
  private readonly channels = new Set<Channel>();
  private nextId = 0;
  private timer?: ReturnType<typeof setTimeout>;
  private heartbeat?: ReturnType<typeof setTimeout>;
  private failures = 0;
  private closed = false;

  constructor(
    private readonly key: string,
    private readonly unauthorized: () => void,
  ) {}

  subscribe(subscription: Subscription) {
    const channel = { ...subscription, id: `channel-${++this.nextId}` };
    this.channels.add(channel);
    channel.state("Connecting");
    if (this.socket?.readyState === WebSocket.OPEN) this.start(channel);
    else this.connect();
    const close = () => {
      this.channels.delete(channel);
      this.send({ kind: "unsubscribe", channel: channel.id });
      if (!this.channels.size) this.disconnect();
    };
    close.restart = () => {
      if (!this.channels.has(channel)) return;
      this.send({ kind: "unsubscribe", channel: channel.id });
      // Ignore late frames from the replaced subscription.
      channel.id = `channel-${++this.nextId}`;
      this.start(channel);
    };
    close.retry = () => {
      if (!this.timer || this.closed) return;
      clearTimeout(this.timer);
      this.timer = undefined;
      this.connect();
    };
    return close;
  }

  private send(command: Omit<Schema<"RealtimeCommand">, "version">) {
    if (this.socket?.readyState === WebSocket.OPEN)
      this.socket.send(JSON.stringify({ version: 1, ...command }));
  }
  private start(channel: Channel) {
    this.send({
      kind: "subscribe",
      channel: channel.id,
      stream: channel.stream,
      root_thread_id: channel.root ?? null,
      after: channel.cursor() ?? null,
    });
  }
  private connect() {
    if (this.closed || this.socket || this.timer || !this.channels.size) return;
    const url = new URL("/api/realtime/connect", window.location.origin);
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    const socket = new WebSocket(url);
    this.socket = socket;
    const alive = () => {
      clearTimeout(this.heartbeat);
      this.heartbeat = setTimeout(() => socket.close(), 60000);
    };
    socket.onopen = () => {
      if (this.socket !== socket) return;
      socket.send(JSON.stringify({ api_key: this.key }));
      for (const channel of this.channels) this.start(channel);
      alive();
    };
    socket.onmessage = ({ data }) => {
      if (this.socket !== socket) return;
      try {
        const message = JSON.parse(String(data));
        if (message.version !== 1)
          throw new Error("Unsupported realtime version");
        alive();
        this.failures = 0;
        if (message.kind === "ping") {
          this.send({ kind: "pong" });
          for (const channel of this.channels) channel.state("Live");
          return;
        }
        const channel = [...this.channels].find(
          (item) => item.id === message.channel,
        );
        if (!channel) return;
        channel.receive(message.frame);
      } catch {
        // Invalid framing cannot safely advance any channel's resume cursor.
        socket.close();
      }
    };
    socket.onclose = ({ code }) => {
      if (this.socket !== socket) return;
      this.socket = undefined;
      clearTimeout(this.heartbeat);
      if (code === 4401) {
        this.close();
        this.unauthorized();
        return;
      }
      for (const channel of this.channels) channel.state("Reconnecting");
      if (this.closed || !this.channels.size) return;
      this.timer = setTimeout(
        () => {
          this.timer = undefined;
          this.connect();
        },
        Math.min(1000 * 2 ** this.failures++, 15000),
      );
    };
  }
  private disconnect() {
    clearTimeout(this.timer);
    clearTimeout(this.heartbeat);
    this.timer = undefined;
    const socket = this.socket;
    this.socket = undefined;
    socket?.close();
  }
  close() {
    this.closed = true;
    this.channels.clear();
    this.disconnect();
  }
}

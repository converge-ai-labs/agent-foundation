import { vi } from "vitest";

export class FakeWebSocket {
  static OPEN = 1;
  static instances: FakeWebSocket[] = [];
  readyState = 0;
  sent: Record<string, unknown>[] = [];
  onopen?: () => void;
  onmessage?: (event: { data: string }) => void;
  onclose?: (event: { code: number }) => void;
  constructor(readonly url: URL) {
    FakeWebSocket.instances.push(this);
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  send(data: string) {
    this.sent.push(JSON.parse(data));
  }
  message(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) });
  }
  frame(
    frame: unknown,
    channel = this.sent.findLast((item) => item.kind === "subscribe")?.channel,
  ) {
    this.message({ version: 1, channel, frame });
  }
  close(code = 1000) {
    this.readyState = 3;
    this.onclose?.({ code });
  }
}
export function mockWebSocket() {
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
  return () => FakeWebSocket.instances.at(-1)!;
}

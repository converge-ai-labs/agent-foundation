import { isRecord, ProtocolError } from "../errors.js";
import type { components } from "../schema.js";
import type { ClientOptions } from "../transport.js";

export type NotificationSubscription =
  components["schemas"]["NotificationSubscription"];
export interface Notification {
  type: "notification";
  schema_version: "1";
  notification_id: string;
  subscription_id: string;
  topic: string;
  workspace_id: string;
  resource_type: string;
  resource_id: string;
  resource_version: number | null;
  session_id: string | null;
  thread_id: string | null;
  run_id: string | null;
  occurred_at: string;
}
export type NotificationState = "connecting" | "connected" | "gap" | "closed";
export interface NotificationOptions {
  subscriptions: NotificationSubscription[];
  onNotification: (notification: Notification) => void;
  /** Every reconnect reports a gap; reconcile with durable Workspace events and current resources. */
  onState: (state: NotificationState) => void;
  onError: (error: Error) => void;
  signal?: AbortSignal;
  /** Node adapters can supply authorization headers. Browser WebSocket only supports session cookies. */
  socketFactory?: (
    url: string,
    protocol: string,
    headers: Readonly<Record<string, string>>,
  ) => WebSocket;
}
const protocol = "a13n.service.notifications.v1";

/** A bounded best-effort attachment. It never owns or cancels Run execution. */
export function notifications(
  client: ClientOptions,
  lifetime: AbortSignal,
  options: NotificationOptions,
): { close(): void } {
  if (client.auth.type === "bearer" && !options.socketFactory) {
    throw new TypeError(
      "Bearer notifications require a socketFactory that supports authorization headers.",
    );
  }
  const signal = options.signal
    ? AbortSignal.any([lifetime, options.signal])
    : lifetime;
  let closed = false;
  let socket: WebSocket | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let attempts = 0;
  const close = () => {
    if (closed) return;
    closed = true;
    clearTimeout(timer);
    signal.removeEventListener("abort", close);
    socket?.close(1000, "client_closed");
    options.onState("closed");
  };
  const fail = (error: Error) => {
    options.onError(error);
    close();
  };
  const connect = async () => {
    if (closed) return;
    options.onState(attempts ? "gap" : "connecting");
    const url = new URL(
      `${client.baseUrl.replace(/\/$/, "")}/api/v1/notifications`,
    );
    url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
    const headers: Record<string, string> = {};
    if (client.auth.type === "bearer") {
      const token = client.auth.token;
      headers.Authorization = `Bearer ${typeof token === "function" ? await token() : token}`;
    }
    if (closed) return;
    const current = options.socketFactory
      ? options.socketFactory(url.href, protocol, headers)
      : new WebSocket(url, protocol);
    socket = current;
    current.onopen = () => {
      if (closed) {
        current.close();
        return;
      }
      current.send(
        JSON.stringify({
          type: "subscribe",
          request_id: `req_${crypto.randomUUID()}`,
          subscriptions: options.subscriptions,
        }),
      );
    };
    current.onmessage = (message) => {
      try {
        if (
          typeof message.data !== "string" ||
          message.data.length > 1024 * 1024
        )
          throw new ProtocolError("Invalid notification frame.");
        const frame: unknown = JSON.parse(message.data);
        if (!isRecord(frame))
          throw new ProtocolError("Invalid notification envelope.");
        if (frame.type === "heartbeat" && typeof frame.nonce === "string") {
          current.send(
            JSON.stringify({ type: "heartbeat_ack", nonce: frame.nonce }),
          );
        } else if (
          frame.type === "subscribed" &&
          Array.isArray(frame.subscription_ids)
        ) {
          options.onState("connected");
        } else if (frame.type === "error") {
          fail(new ProtocolError(`${frame.code}: ${frame.message}`));
        } else if (
          frame.type === "notification" &&
          frame.schema_version === "1" &&
          [
            "notification_id",
            "subscription_id",
            "topic",
            "workspace_id",
            "resource_type",
            "resource_id",
            "occurred_at",
          ].every((key) => typeof frame[key] === "string") &&
          (frame.resource_version === null ||
            typeof frame.resource_version === "number") &&
          ["session_id", "thread_id", "run_id"].every(
            (key) => frame[key] === null || typeof frame[key] === "string",
          )
        ) {
          options.onNotification(frame as unknown as Notification);
        } else {
          throw new ProtocolError("Unsupported notification envelope.");
        }
      } catch (error) {
        fail(
          error instanceof Error
            ? error
            : new ProtocolError("Invalid notification frame."),
        );
      }
    };
    // onclose carries the meaningful outcome; browser error events contain no safe detail.
    current.onerror = () => undefined;
    current.onclose = (event) => {
      if (closed) return;
      if ([1002, 1003, 1008, 1009].includes(event.code) || attempts >= 3) {
        fail(
          new ProtocolError(`Notification connection closed (${event.code}).`),
        );
        return;
      }
      options.onState("gap");
      timer = setTimeout(
        () => {
          void connect().catch((error) => fail(error));
        },
        250 * 2 ** attempts++,
      );
    };
  };
  signal.addEventListener("abort", close, { once: true });
  if (signal.aborted) close();
  else void connect().catch((error) => fail(error));
  return { close };
}

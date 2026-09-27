import { result, type Schema, type Transport } from "../transport/client";

export type MessageState = {
  request: Schema<"AppMessageRequest">;
  context?: Schema<"AppContext">;
  sent: boolean;
  receipt?: Schema<"AppMessageReceipt">;
  uncertain?: string;
};

/** One user-reviewed message at a time. Lost writes are reconciled by key, never resent. */
export class AppMessage {
  private state?: MessageState;
  private listeners = new Set<() => void>();
  private waiting?: {
    resolve: (value: Schema<"RootRunReceipt">) => void;
    reject: (error: Error) => void;
  };
  private timer?: ReturnType<typeof setTimeout>;
  private closed = false;
  constructor(
    private transport: Transport,
    private view: Schema<"AppView">,
  ) {}
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  getSnapshot = () => this.state;
  private publish(state: MessageState | undefined) {
    this.state = state;
    for (const listener of this.listeners) listener();
  }
  private path() {
    return {
      thread_id: this.view.reference.thread_id,
      view_id: this.view.view_id,
      request_key: this.state!.request.request_key,
    };
  }
  propose(
    content: { type: "text"; text: string }[],
    context?: Schema<"AppContext">,
  ): Promise<Schema<"RootRunReceipt">> {
    if (this.closed || this.waiting)
      return Promise.reject(
        new Error(
          "This View is closed or already has a pending message. Messages are not queued.",
        ),
      );
    const request: Schema<"AppMessageRequest"> = {
      request_key: crypto.randomUUID(),
      role: "user",
      content: JSON.parse(JSON.stringify(content)),
      ...(context ? { context: context.reference } : {}),
    };
    if (
      !content.some((item) => item.text.trim()) ||
      new TextEncoder().encode(JSON.stringify(request)).length > 64 * 1024
    )
      return Promise.reject(
        new Error("App messages must contain text and fit within 64 KiB."),
      );
    const completion = new Promise<Schema<"RootRunReceipt">>(
      (resolve, reject) => {
        this.waiting = { resolve, reject };
      },
    );
    this.publish({ request, context, sent: false });
    return completion;
  }
  decline() {
    if (!this.state || this.state.sent) return;
    this.waiting?.reject(new Error("The user declined this App message."));
    this.waiting = undefined;
    this.publish(undefined);
  }
  async confirm() {
    if (this.closed || !this.state || this.state.sent) return;
    this.publish({ ...this.state, sent: true });
    try {
      this.observe(
        await result(
          this.transport.client.POST(
            "/api/threads/{thread_id}/apps/{view_id}/messages",
            { params: { path: this.path() }, body: this.state!.request },
          ),
        ),
      );
    } catch {
      await this.reconcile();
    }
  }
  private observe(receipt: Schema<"AppMessageReceipt">) {
    if (
      this.closed ||
      !this.state ||
      receipt.request.request_key !== this.state.request.request_key
    )
      return;
    clearTimeout(this.timer);
    this.publish({ ...this.state, receipt, uncertain: undefined });
    if (receipt.status === "submitting") {
      this.timer = setTimeout(() => void this.reconcile(), 250);
    } else {
      if (receipt.status === "accepted" && receipt.receipt)
        this.waiting?.resolve(receipt.receipt);
      else
        this.waiting?.reject(
          new Error(receipt.reason ?? "The App message was not accepted."),
        );
      this.waiting = undefined;
    }
  }
  async reconcile() {
    if (this.closed || !this.state?.sent) return;
    const key = this.state.request.request_key;
    clearTimeout(this.timer);
    try {
      this.observe(
        await result(
          this.transport.client.GET(
            "/api/threads/{thread_id}/apps/{view_id}/messages/{request_key}",
            { params: { path: this.path() } },
          ),
        ),
      );
    } catch {
      if (!this.closed && this.state?.request.request_key === key)
        this.publish({
          ...this.state,
          uncertain:
            "Message outcome unknown. It was not resent. Check its receipt or inspect the conversation.",
        });
    }
  }
  close() {
    this.closed = true;
    clearTimeout(this.timer);
    this.waiting?.reject(
      new Error(
        "The App View closed. A confirmed message may already have been accepted.",
      ),
    );
    this.waiting = undefined;
  }
}

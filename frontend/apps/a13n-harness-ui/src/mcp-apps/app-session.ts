import {
  CallToolResultSchema,
  ReadResourceResultSchema,
} from "@modelcontextprotocol/core";
import type { CallToolResult } from "@modelcontextprotocol/client";
import { result, type Schema, type Transport } from "../transport/client";
import { AppMessage } from "./app-message";

export type AppRequestState = {
  request: Schema<"AppToolRequest">;
  operation?: Schema<"AppOperation">;
  uncertain?: string;
  deciding?: boolean;
  submittedDecision?: boolean;
};
export type AppContextState = {
  context?: Schema<"AppContext">;
  selected: boolean;
  pending: boolean;
};
type Pending = {
  resolve: (result: CallToolResult) => void;
  reject: (error: Error) => void;
  timer?: ReturnType<typeof setTimeout>;
};

/** One browser View's exact requests. Reconciliation only reads; it never repeats a write. */
export class AppSession {
  private pending = new Map<string, Pending>();
  private listeners = new Set<() => void>();
  private states: AppRequestState[] = [];
  private closed = false;
  private contextState: AppContextState = { selected: false, pending: false };
  private contextWork = Promise.resolve();
  private contextSequence = 0;
  readonly messages: AppMessage;

  constructor(
    private transport: Transport,
    readonly view: Schema<"AppView">,
  ) {
    this.messages = new AppMessage(transport, view);
  }

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  getSnapshot = () => this.states;
  getContextSnapshot = () => this.contextState;
  private setContext(changes: Partial<AppContextState>) {
    this.contextState = { ...this.contextState, ...changes };
    for (const listener of this.listeners) listener();
  }
  selectContext(selected: boolean) {
    this.setContext({ selected });
  }
  captureContext(): Schema<"AppContextReference"> | undefined {
    if (this.closed || !this.contextState.selected) return undefined;
    if (this.contextState.pending)
      throw new Error(
        "Wait for the selected App context update before sending.",
      );
    return this.contextState.context?.reference;
  }
  updateContext(value: unknown): Promise<void> {
    if (this.closed || this.contextState.pending)
      return Promise.reject(
        new Error(
          "The App View is closed or a context update is still pending.",
        ),
      );
    const encoded = JSON.stringify(value);
    if (new TextEncoder().encode(encoded).length > 64 * 1024)
      return Promise.reject(new Error("App context exceeds 64 KiB."));
    const body: Schema<"AppContextUpdate"> = JSON.parse(encoded);
    const sequence = ++this.contextSequence;
    this.setContext({ pending: true });
    const update = this.contextWork.then(async () => {
      if (this.closed) throw new Error("This App View is closed.");
      const context = await result(
        this.transport.client.PUT(
          "/api/threads/{thread_id}/apps/{view_id}/context",
          { params: { path: this.path("") }, body },
        ),
      );
      if (!this.closed && sequence === this.contextSequence)
        this.setContext({ context, pending: false });
    });
    this.contextWork = update.catch(() => {
      if (sequence === this.contextSequence)
        this.setContext({
          context: undefined,
          selected: false,
          pending: false,
        });
    });
    return update;
  }
  discardContext(): Promise<void> {
    ++this.contextSequence;
    this.setContext({ context: undefined, selected: false, pending: false });
    const discard = this.contextWork.then(async () => {
      await this.transport.client.DELETE(
        "/api/threads/{thread_id}/apps/{view_id}/context",
        { params: { path: this.path("") } },
      );
    });
    this.contextWork = discard.catch(() => {});
    return discard;
  }
  private path(request_key: string) {
    return {
      thread_id: this.view.reference.thread_id,
      view_id: this.view.view_id,
      request_key,
    };
  }
  private update(key: string, changes: Partial<AppRequestState>) {
    this.states = this.states.map((state) =>
      state.request.request_key === key ? { ...state, ...changes } : state,
    );
    for (const listener of this.listeners) listener();
  }

  call(
    name: string,
    args: Record<string, unknown> = {},
  ): Promise<CallToolResult> {
    if (this.closed)
      return Promise.reject(new Error("This App View is closed."));
    if (this.states.length >= 128)
      return Promise.reject(
        new Error("Reopen this App View before submitting more operations."),
      );
    // The wire payload is captured once, before any asynchronous work.
    const request: Schema<"AppToolRequest"> = {
      request_key: crypto.randomUUID(),
      name,
      arguments: JSON.parse(JSON.stringify(args)),
    };
    if (new TextEncoder().encode(JSON.stringify(request)).length > 256 * 1024)
      return Promise.reject(new Error("The App request exceeds 256 KiB."));
    const completion = new Promise<CallToolResult>((resolve, reject) => {
      this.pending.set(request.request_key, { resolve, reject });
    });
    this.states = [...this.states, { request }];
    for (const listener of this.listeners) listener();
    void result(
      this.transport.client.POST(
        "/api/threads/{thread_id}/apps/{view_id}/tools",
        {
          params: { path: this.path(request.request_key) },
          body: request,
        },
      ),
    )
      .then((operation) => this.observe(operation))
      .catch(() => this.reconcile(request.request_key));
    return completion;
  }

  private observe(operation: Schema<"AppOperation">) {
    if (this.closed) return;
    const key = operation.request_key;
    const state = this.states.find(
      (value) => value.request.request_key === key,
    );
    const unconfirmedDecision =
      operation.status === "approval_required" &&
      state?.submittedDecision !== undefined;
    this.update(key, {
      operation,
      uncertain: unconfirmedDecision
        ? "Decision not confirmed. It will not be repeated or changed. Check the result or close this View."
        : undefined,
      deciding: false,
    });
    const pending = this.pending.get(key);
    if (!pending) return;
    clearTimeout(pending.timer);
    if (operation.status === "checking" || operation.status === "running") {
      pending.timer = setTimeout(() => void this.reconcile(key), 250);
    } else if (operation.status !== "approval_required") {
      this.pending.delete(key);
      const parsed = CallToolResultSchema.safeParse(operation.result);
      if (operation.status === "completed" && parsed.success)
        pending.resolve(parsed.data);
      else
        pending.reject(
          new Error(
            operation.reason ??
              "The App operation did not return a usable result.",
          ),
        );
    }
  }

  async reconcile(key: string) {
    if (this.closed) return;
    const pending = this.pending.get(key);
    clearTimeout(pending?.timer);
    try {
      this.observe(
        await result(
          this.transport.client.GET(
            "/api/threads/{thread_id}/apps/{view_id}/operations/{request_key}",
            { params: { path: this.path(key) } },
          ),
        ),
      );
    } catch (error) {
      if (!this.closed)
        this.update(key, {
          deciding: false,
          uncertain: `Result unknown. The request was not repeated. ${error instanceof Error ? error.message : String(error)}`,
        });
    }
  }

  async decide(key: string, approve: boolean) {
    const state = this.states.find(
      (value) => value.request.request_key === key,
    );
    if (
      this.closed ||
      state?.operation?.status !== "approval_required" ||
      state.deciding ||
      state.submittedDecision !== undefined ||
      state.uncertain
    )
      return;
    this.update(key, { deciding: true, submittedDecision: approve });
    try {
      this.observe(
        await result(
          this.transport.client.POST(
            "/api/threads/{thread_id}/apps/{view_id}/operations/{request_key}/decision",
            { params: { path: this.path(key) }, body: { approve } },
          ),
        ),
      );
    } catch {
      await this.reconcile(key);
    }
  }

  async readResource(uri: string) {
    if (this.closed) throw new Error("This App View is closed.");
    return ReadResourceResultSchema.parse(
      await result(
        this.transport.client.POST(
          "/api/threads/{thread_id}/apps/{view_id}/resources/read",
          { params: { path: this.path("") }, body: { uri } },
        ),
      ),
    );
  }

  async close() {
    if (this.closed) return;
    this.closed = true;
    this.messages.close();
    this.setContext({ context: undefined, selected: false, pending: false });
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(
        new Error(
          "The App View closed. Already sent operations may still complete.",
        ),
      );
    }
    this.pending.clear();
    await this.transport.client.DELETE(
      "/api/threads/{thread_id}/apps/{view_id}",
      {
        params: { path: this.path("") },
      },
    );
  }
}

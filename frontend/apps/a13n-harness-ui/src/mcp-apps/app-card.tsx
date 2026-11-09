import { useTranslation } from "../i18n";
import { errorMessage } from "../i18n/error-message";
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { refreshThread } from "../conversations/refresh";
import { CallToolResultSchema } from "@modelcontextprotocol/core";
import { McpUiResourceCspSchema } from "@modelcontextprotocol/ext-apps/app-bridge";
import { Button } from "a13n-ui";
import { AppFrame, type AppHandlers } from "./app-frame";
import { AppSession } from "./app-session";
import { useAppLink } from "./app-link";
import { useAppContextSelection } from "./context-selection";
import { result, type Schema } from "../transport/client";
import { useGuestAccess, useTransport } from "../transport/context";
import styles from "./app-card.module.css";

const capabilities = {
  serverTools: {},
  serverResources: {},
  openLinks: {},
  updateModelContext: { text: {} },
  message: { text: {} },
};

export function AppCard(props: {
  reference: Schema<"AppReference">;
  live?: boolean;
}) {
  const { t } = useTranslation();
  const guest = useGuestAccess();
  return guest ? (
    <p>{t("Interactive app - available to the instance owner")}</p>
  ) : (
    <OwnerAppCard {...props} />
  );
}

function OwnerAppCard({
  reference,
  live = false,
}: {
  reference: Schema<"AppReference">;
  live?: boolean;
}) {
  const { t } = useTranslation();
  const transport = useTransport();
  const queries = useQueryClient();
  const [opened, setOpened] = useState(live && !reference.unavailable);
  const [session, setSession] = useState<AppSession>();
  const contexts = useAppContextSelection();
  useEffect(
    () => (session ? contexts?.register(session) : undefined),
    [session, contexts],
  );
  const active = useRef<AppSession | undefined>(undefined);
  const generation = useRef(0);
  const [activating, setActivating] = useState(false);
  const [error, setError] = useState<{ cause: unknown } | { closed: true }>();
  useEffect(
    () => () => {
      generation.current++;
      void active.current?.close().catch(() => {});
    },
    [],
  );
  const {
    propose: proposeLink,
    cancel: cancelLink,
    confirmation: linkConfirmation,
  } = useAppLink();
  const presentation = useQuery({
    queryKey: ["mcp-app", reference],
    queryFn: () =>
      result(
        transport.client.POST("/api/threads/{thread_id}/apps/open", {
          params: { path: { thread_id: reference.thread_id } },
          body: reference,
        }),
      ),
    enabled: opened,
    staleTime: Infinity,
    retry: false,
  });
  const handlers: AppHandlers = useMemo(
    () => ({
      onopenlink: async ({ url }) => {
        if (!presentation.data?.sandbox_url)
          throw new Error("The App is not open.");
        return proposeLink(url, presentation.data.sandbox_url);
      },
      onmessage: async ({ content }) => {
        if (!session)
          throw new Error("Turn on server interactions in the app card first.");
        const text = content.map((part) => {
          if (part.type !== "text")
            throw new Error("This host accepts text App messages only.");
          return { type: "text" as const, text: part.text };
        });
        const selected = session.captureContext()
          ? session.getContextSnapshot().context
          : undefined;
        const receipt = await session.messages.propose(text, selected);
        refreshThread(queries, receipt.thread_id, "lifecycle");
        return {};
      },
      onupdatemodelcontext: async (value) => {
        if (!session)
          throw new Error("Turn on server interactions in the app card first.");
        if (value.content?.some((part) => part.type !== "text"))
          throw new Error(
            "This host accepts text and structured App context only.",
          );
        await session.updateContext(value);
        return {};
      },
      onreadresource: ({ uri }) => {
        if (!session)
          return Promise.reject(
            new Error("Turn on server interactions in the app card first."),
          );
        return session.readResource(uri);
      },
      oncalltool: ({ name, arguments: args }) => {
        if (!session)
          return Promise.reject(
            new Error("Turn on server interactions in the app card first."),
          );
        return session.call(name, args);
      },
    }),
    [session, queries, presentation.data?.sandbox_url, proposeLink],
  );
  async function activate() {
    const attempt = ++generation.current;
    setActivating(true);
    setError(undefined);
    try {
      const view = await result(
        transport.client.POST("/api/threads/{thread_id}/apps/activate", {
          params: { path: { thread_id: reference.thread_id } },
          body: reference,
        }),
      );
      const next = new AppSession(transport, view);
      if (generation.current !== attempt) {
        void next.close().catch(() => {});
        return;
      }
      active.current = next;
      setSession(next);
    } catch (cause) {
      if (generation.current === attempt) setError({ cause });
    } finally {
      if (generation.current === attempt) setActivating(false);
    }
  }
  function close() {
    cancelLink();
    generation.current++;
    setActivating(false);
    setOpened(false);
    setSession(undefined);
    const previous = active.current;
    active.current = undefined;
    void previous?.close().catch(() => setError({ closed: true }));
  }
  const data = presentation.data;
  const parsed = useMemo(() => {
    if (!data?.resource) return undefined;
    const ui = data.resource.metadata?.ui;
    const csp = McpUiResourceCspSchema.safeParse(
      ui && typeof ui === "object" && !Array.isArray(ui) && "csp" in ui
        ? ui.csp
        : {},
    );
    const result = CallToolResultSchema.safeParse(data.snapshot.result);
    return csp.success && result.success
      ? { result: result.data, csp: csp.data }
      : undefined;
  }, [data]);
  return (
    <section
      className={styles.card}
      aria-label={t("MCP App: {{name}}", { name: reference.tool_name })}
    >
      <header className={styles.header}>
        <div>
          <strong>{reference.tool_name}</strong>
          <small>{reference.server_id} - MCP App</small>
        </div>
        <Button
          size="sm"
          variant="ghost"
          disabled={Boolean(reference.unavailable)}
          onClick={() => (opened ? close() : setOpened(true))}
        >
          {opened ? t("Close view") : t("Open App")}
        </Button>
      </header>
      {reference.unavailable && <p role="status">{reference.unavailable}</p>}
      {error && (
        <p role="alert">
          {"closed" in error
            ? t(
                "This view closed, but the server didn't confirm. Actions already sent may still finish.",
              )
            : errorMessage(error.cause, t)}
        </p>
      )}
      {!opened && !reference.unavailable && (
        <p>{t("Reopen the app. The tool won't run again.")}</p>
      )}
      {opened && presentation.isPending && (
        <p role="status">{t("Loading App…")}</p>
      )}
      {opened && presentation.error && (
        <div className={styles.status}>
          <p role="alert">{errorMessage(presentation.error, t)}</p>
          <Button
            size="sm"
            variant="outline"
            disabled={presentation.isFetching}
            onClick={() => void presentation.refetch()}
          >
            {presentation.isFetching ? t("Retrying…") : t("Try again")}
          </Button>
        </div>
      )}
      {opened && data && (!data.resource || !parsed || !data.sandbox_url) && (
        <p role="alert">
          {t("The original App presentation cannot be displayed.")}
        </p>
      )}
      {opened && data?.resource && data.sandbox_url && parsed && (
        <>
          <div className={styles.status}>
            <p>
              {session
                ? t(
                    "Server interactions active - follow-up results stay in this View",
                  )
                : t(
                    "Original presentation - server interactions are not activated",
                  )}
            </p>
            {!session && (
              <Button
                size="sm"
                variant="outline"
                disabled={activating}
                onClick={() => void activate()}
              >
                {activating ? t("Activating…") : t("Enable controls")}
              </Button>
            )}
          </div>
          {session && (
            <>
              <AppRequests session={session} />
              <AppMessageConfirmation session={session} />
              <AppModelContext session={session} selectable={!!contexts} />
            </>
          )}
          {linkConfirmation}
          <AppFrame
            title={reference.tool_name}
            sandboxUrl={data.sandbox_url}
            html={data.resource.html}
            csp={parsed.csp}
            arguments={data.snapshot.arguments}
            result={parsed.result}
            capabilities={capabilities}
            handlers={handlers}
          />
        </>
      )}
    </section>
  );
}

function AppMessageConfirmation({ session }: { session: AppSession }) {
  const { t } = useTranslation();
  const message = session.messages;
  const state = useSyncExternalStore(message.subscribe, message.getSnapshot);
  if (!state) return null;
  return (
    <section
      className={styles.request}
      aria-label={t("App message confirmation")}
    >
      <strong>
        {session.view.route?.length
          ? t("Send child App handoff to the owning chat?")
          : t("Send this App message to the chat?")}
      </strong>
      <p>
        {session.view.reference.server_id} / {session.view.reference.tool_name}{" "}
        → {session.view.root_thread_id}
      </p>
      <p>
        {t(
          "Starts a new message without changing your draft. It can't interrupt active work or wait in a busy chat's queue.",
        )}
      </p>
      <pre>{state.request.content.map((part) => part.text).join("\n")}</pre>
      {state.context && (
        <details>
          <summary>{t("App context")}</summary>
          <pre>{JSON.stringify(state.context.value, null, 2)}</pre>
        </details>
      )}
      {state.uncertain && (
        <p role="alert">
          {t(
            "Message outcome unknown. It was not resent. Check its receipt or inspect the chat.",
          )}
        </p>
      )}
      {state.receipt?.reason && <p role="alert">{state.receipt.reason}</p>}
      {state.receipt?.receipt && (
        <p role="status">
          {t("Accepted by the chat - {{id}}", {
            id: state.receipt.receipt.receipt_id,
          })}
        </p>
      )}
      {!state.sent ? (
        <div className={styles.actions}>
          <Button size="sm" onClick={() => void message.confirm()}>
            {t("Send once")}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => message.decline()}>
            {t("Decline message")}
          </Button>
        </div>
      ) : state.uncertain ? (
        <Button
          size="sm"
          variant="outline"
          onClick={() => void message.reconcile()}
        >
          {t("Check delivery")}
        </Button>
      ) : state.receipt?.status !== "accepted" &&
        state.receipt?.status !== "failed" ? (
        <p role="status">{t("Confirming admission…")}</p>
      ) : null}
    </section>
  );
}

function AppModelContext({
  session,
  selectable,
}: {
  session: AppSession;
  selectable: boolean;
}) {
  const { t } = useTranslation();
  const state = useSyncExternalStore(
    session.subscribe,
    session.getContextSnapshot,
  );
  const [error, setError] = useState<string>();
  if (!state.context && !state.pending && !error) return null;
  return (
    <section className={styles.request} aria-label={t("App model context")}>
      <div className={styles.requestHeading}>
        <strong>{t("App context")}</strong>
        <span>{state.pending ? t("Updating…") : t("Latest value")}</span>
      </div>
      <p>
        {t(
          "This selection will go with your next message. Nothing is sent automatically, and other browsers' selections aren't included.",
        )}
      </p>
      {state.context && (
        <>
          <details>
            <summary>{t("Show app context")}</summary>
            <pre>{JSON.stringify(state.context.value, null, 2)}</pre>
          </details>
          <label>
            <input
              type="checkbox"
              checked={state.selected}
              disabled={state.pending || !selectable}
              onChange={(event) => session.selectContext(event.target.checked)}
            />{" "}
            {t("Include in my next messages")}
          </label>
        </>
      )}
      <Button
        size="sm"
        variant="ghost"
        onClick={() => {
          setError(undefined);
          void session
            .discardContext()
            .catch(() =>
              setError(
                "Context was removed from this browser's selection, but server confirmation was lost.",
              ),
            );
        }}
      >
        {t("Discard context")}
      </Button>
      {error && (
        <p role="alert">
          {error ===
          "This view closed, but the server didn't confirm. Actions already sent may still finish."
            ? t(
                "This view closed, but the server didn't confirm. Actions already sent may still finish.",
              )
            : error ===
                "Context was removed from this browser's selection, but server confirmation was lost."
              ? t(
                  "Context was removed from this browser's selection, but server confirmation was lost.",
                )
              : error}
        </p>
      )}
    </section>
  );
}

function AppRequests({ session }: { session: AppSession }) {
  const { t } = useTranslation();
  const requests = useSyncExternalStore(session.subscribe, session.getSnapshot);
  const statusLabels: Record<Schema<"AppOperation">["status"], string> = {
    checking: t("Checking"),
    approval_required: t("Approval required"),
    running: t("Running"),
    completed: t("Completed"),
    denied: t("Denied"),
    failed: t("Failed"),
  };
  const pending = requests.filter(
    ({ operation, uncertain }) =>
      uncertain ||
      !operation ||
      ["checking", "approval_required", "running"].includes(operation.status),
  );
  const completed = requests.filter((item) => !pending.includes(item));
  const render = (items: typeof requests) =>
    items.map(({ request, operation, uncertain, deciding }) => (
      <section
        key={request.request_key}
        className={styles.request}
        aria-label={t("App request: {{name}}", { name: request.name })}
      >
        <div className={styles.requestHeading}>
          <strong>
            {operation?.tool_id ??
              `${session.view.reference.server_id} / ${request.name}`}
          </strong>
          <span>
            {uncertain
              ? t("Result unknown")
              : operation
                ? statusLabels[operation.status]
                : t("Submitting")}
          </span>
        </div>
        <pre aria-label={t("Exact App arguments")}>
          {JSON.stringify(operation?.arguments ?? request.arguments, null, 2)}
        </pre>
        {operation?.reason && <p>{operation.reason}</p>}
        {uncertain && (
          <p role="alert">
            {uncertain.kind === "decision" ? (
              t(
                "Couldn't confirm your choice. Nothing was retried or changed. Check the result or close this view.",
              )
            ) : (
              <>
                {t("Result unknown. The request was not repeated.")}{" "}
                {errorMessage(uncertain.cause, t)}
              </>
            )}
          </p>
        )}
        <div className={styles.actions}>
          {uncertain ? (
            <Button
              size="sm"
              variant="outline"
              onClick={() => void session.reconcile(request.request_key)}
            >
              {t("Check result")}
            </Button>
          ) : (
            operation?.status === "approval_required" && (
              <>
                <Button
                  size="sm"
                  disabled={deciding}
                  onClick={() => void session.decide(request.request_key, true)}
                >
                  {t("Approve once")}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={deciding}
                  onClick={() =>
                    void session.decide(request.request_key, false)
                  }
                >
                  {t("Deny")}
                </Button>
              </>
            )
          )}
        </div>
      </section>
    ));
  return (
    <div className={styles.requests}>
      {render(pending)}
      {completed.length > 0 && (
        <details>
          <summary>
            {t("Recent App operations ({{count}})", {
              count: completed.length,
            })}
          </summary>
          {render(completed)}
        </details>
      )}
    </div>
  );
}

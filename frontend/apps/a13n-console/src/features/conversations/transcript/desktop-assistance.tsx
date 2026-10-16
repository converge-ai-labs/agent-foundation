import { Button, ChoiceField } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../../shared/api";
import { ErrorNotice } from "../../../shared/feedback";
import { ApiError } from "../../../service-client";
import { TextAreaField } from "../../../shared/forms";
import styles from "./desktop-assistance.module.css";
import type RFB from "@novnc/novnc/lib/rfb";

/** Credentials live only in this mounted viewer; never in query caches or storage. */
export function DesktopAssistance({
  runId,
  callId,
  onResolved = () => {},
  mounts,
  canContinue = false,
  onContinued,
  onRefresh,
}: {
  runId: string;
  callId?: string;
  mounts?: string[];
  canContinue?: boolean;
  onContinued?: (run: Schema["RunView"]) => void;
  onRefresh?: () => unknown;
  onResolved?: (value: Schema["JsonValue"]) => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace();
  const api = client.workspace(workspace.id);
  const [selectedMount, setSelectedMount] = useState(
    mounts?.[0] ?? "workspace",
  );
  const [sessionId] = useState(() => crypto.randomUUID());
  const [access, setAccess] = useState<Schema["Access"] | null>(null);
  const [transition, setTransition] = useState<string | null>(null);
  const [claimRequested, setClaimRequested] = useState(false);
  const [agentRequested, setAgentRequested] = useState(false);
  const refresh = useRef(onRefresh);
  refresh.current = onRefresh;
  const [busy, setBusy] = useState(false),
    [error, setError] = useState<unknown>(null);
  const [connected, setConnected] = useState(false),
    [note, setNote] = useState("");
  const screen = useRef<HTMLDivElement>(null),
    rfb = useRef<RFB | null>(null);
  const renewal = useRef<Promise<unknown> | null>(null);
  const current = useRef(access);
  current.current = access;
  const epoch = useRef(0);
  const mounted = useRef(true);
  const resolve = useRef(onResolved);
  resolve.current = onResolved;
  const query = useQuery({
    queryKey: ["desktop-assistance", workspace.id, runId, callId],
    queryFn: async () =>
      !callId
        ? data(
            await api.GET("/api/v1/runs/{run_id}/desktop-control", {
              params: { path: { run_id: runId } },
            }),
          )
        : data(
            await api.GET(
              "/api/v1/runs/{run_id}/desktop-assistance/{call_id}",
              {
                params: { path: { run_id: runId, call_id: callId } },
              },
            ),
          ),
    refetchInterval: 3000,
  });
  const view = query.data;
  if (view && !access) epoch.current = view.epoch;
  useEffect(() => {
    if (view?.outcome && ["ready_to_resume", "abandoned"].includes(view.state))
      resolve.current(view.outcome);
  }, [view]);

  function closeLocal() {
    current.current = null;
    if (rfb.current) {
      rfb.current.viewOnly = true;
      rfb.current.disconnect();
      rfb.current = null;
    }
    setAccess(null);
    setConnected(false);
  }
  const cleanup = useRef(() => {});
  cleanup.current = () => {
    const grant = current.current;
    if (rfb.current) {
      rfb.current.viewOnly = true;
      rfb.current.disconnect();
      rfb.current = null;
    }
    if (grant)
      void api
        .POST("/api/v1/desktop-assistance/{assistance_id}/disconnect", {
          params: { path: { assistance_id: grant.assistance.id } },
          body: { session_id: sessionId, epoch: grant.assistance.epoch },
        })
        .catch(() => {}); // Server TTL is the fence if the browser can no longer send.
  };
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      cleanup.current();
    };
  }, []);

  useEffect(() => {
    if (!agentRequested) return;
    // The Agent may have reserved the desktop but not sealed its deferred batch yet.
    // Refresh until RunDock can replace this panel with the authoritative assistance card.
    const update = () => {
      void Promise.resolve(refresh.current?.()).catch((cause) => {
        if (mounted.current) setError(cause);
      });
    };
    update();
    const timer = setInterval(update, 3000);
    return () => clearInterval(timer);
  }, [agentRequested]);

  function failed(cause: unknown) {
    if (!mounted.current) return;
    if (
      !callId &&
      cause instanceof ApiError &&
      cause.status === 409 &&
      cause.details.reason === "desktop_agent_assistance_pending"
    ) {
      setClaimRequested(false);
      setAgentRequested(true);
    } else setError(cause);
  }

  useEffect(() => {
    if (!access) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    let expiry: ReturnType<typeof setTimeout>;
    const grant = access;
    const stop = (message: string) => {
      if (!active) return;
      closeLocal();
      setError(new Error(t(message)));
    };
    const armExpiry = (seconds: number) => {
      clearTimeout(expiry);
      expiry = setTimeout(
        () => stop("Desktop access expired. Open it again to reconnect."),
        Math.max(0, seconds * 1000 - 1500),
      );
    };
    armExpiry(grant.expires_in_seconds);
    void import("@novnc/novnc/lib/rfb")
      .then(({ default: imported }) => {
        // noVNC's published CommonJS package keeps the class under exports.default.
        const value: unknown = imported;
        const Viewer = (
          typeof value === "function"
            ? value
            : (value as { default: typeof RFB }).default
        ) as typeof RFB;
        if (!active || !screen.current) return;
        const instance = new Viewer(screen.current, grant.websocket_url, {
          credentials: { password: grant.password },
          shared: true,
        });
        rfb.current = instance;
        instance.viewOnly = true;
        instance.scaleViewport = true;
        instance.addEventListener("connect", () => {
          if (!active) return;
          instance.viewOnly = grant.mode !== "control";
          setConnected(true);
        });
        instance.addEventListener("disconnect", () =>
          stop("Desktop disconnected. Open it again to reconnect."),
        );
        instance.addEventListener("securityfailure", () =>
          stop("Desktop connection was refused."),
        );
      })
      .catch(() => stop("Desktop connection was refused."));
    async function renew() {
      if (!active || current.current !== grant) return;
      const pending = api.POST(
        grant.mode === "control"
          ? "/api/v1/desktop-assistance/{assistance_id}/control"
          : "/api/v1/desktop-assistance/{assistance_id}/view",
        {
          params: {
            path: { assistance_id: grant.assistance.id },
            query: { renew: true },
          },
          body: { session_id: sessionId, epoch: grant.assistance.epoch },
        },
      );
      renewal.current = pending;
      try {
        const response = data(await pending);
        if (!active || current.current !== grant) return;
        armExpiry(response.expires_in_seconds);
        timer = setTimeout(() => void renew(), 15000);
      } catch {
        stop("Desktop access could not be renewed. Input has been disabled.");
      } finally {
        if (renewal.current === pending) renewal.current = null;
      }
    }
    timer = setTimeout(() => void renew(), 15000);
    return () => {
      active = false;
      clearTimeout(timer);
      clearTimeout(expiry);
      if (rfb.current) {
        rfb.current.viewOnly = true;
        rfb.current.disconnect();
        rfb.current = null;
      }
    };
    // A grant defines one connection; metadata polling never reconnects it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [access]);

  async function continueAgent(identity: string) {
    const next = data(
      await api.POST("/api/v1/runs/{run_id}/resume", {
        params: {
          path: { run_id: runId },
          header: commandHeaders(`desktop-return-${identity}`),
        },
        body: { approvals: {}, calls: {} },
      }),
    );
    onContinued?.(next);
  }

  async function command(
    action:
      "view" | "control" | "disconnect" | "complete" | "request" | "continue",
  ) {
    if (!view || busy) return;
    setBusy(true);
    setTransition(action);
    setError(null);
    // Stop local input before any transition, including failures and slow requests.
    const previousAccess = current.current;
    closeLocal();
    try {
      // Local input stops immediately; let an admitted renewal release its server transition first.
      await renewal.current?.catch(() => {});
      if (!mounted.current) return;
      const params = { path: { assistance_id: view.id } };
      const body = { session_id: sessionId, epoch: epoch.current };
      if (action === "request") {
        if (previousAccess?.mode === "view") {
          data(
            await api.POST(
              "/api/v1/desktop-assistance/{assistance_id}/disconnect",
              {
                params,
                body: {
                  session_id: sessionId,
                  epoch: previousAccess.assistance.epoch,
                },
              },
            ),
          );
        }
        data(
          await api.POST(
            "/api/v1/desktop-assistance/{assistance_id}/request-control",
            { params },
          ),
        );
        if (mounted.current) setClaimRequested(true);
      } else if (action === "continue") {
        await continueAgent(view.id);
      } else if (action === "view" || action === "control") {
        const next = data(
          await api.POST(
            action === "view"
              ? "/api/v1/desktop-assistance/{assistance_id}/view"
              : "/api/v1/desktop-assistance/{assistance_id}/control",
            { params, body },
          ),
        );
        if (mounted.current) {
          epoch.current = next.assistance.epoch;
          setAccess(next);
        } else
          void api
            .POST("/api/v1/desktop-assistance/{assistance_id}/disconnect", {
              params,
              body: { ...body, epoch: next.assistance.epoch },
            })
            .catch(() => {});
      } else if (action === "complete") {
        const next = data(
          await api.POST(
            "/api/v1/desktop-assistance/{assistance_id}/complete",
            {
              params,
              body: { ...body, outcome: "completed", note },
            },
          ),
        );
        if (mounted.current && next.outcome) resolve.current(next.outcome);
        if (!callId && canContinue) {
          await continueAgent(view.id);
        }
      } else
        data(
          await api.POST(
            "/api/v1/desktop-assistance/{assistance_id}/disconnect",
            { params, body },
          ),
        );
      if (mounted.current) await query.refetch();
    } catch (cause) {
      failed(cause);
    } finally {
      if (mounted.current) {
        setBusy(false);
        setTransition(null);
      }
    }
  }
  useEffect(() => {
    if (
      claimRequested &&
      !agentRequested &&
      view?.state === "awaiting_user" &&
      !busy
    ) {
      setClaimRequested(false);
      void command("control");
    }
    // Only an explicit click in this browser may acquire a control grant.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [claimRequested, agentRequested, view?.state, busy]);

  async function openDesktop() {
    setBusy(true);
    setError(null);
    try {
      data(
        await api.POST("/api/v1/runs/{run_id}/desktop-control", {
          params: { path: { run_id: runId } },
          body: {
            mount: selectedMount,
            reason: "User-initiated desktop control",
          },
        }),
      );
      await query.refetch();
    } catch (cause) {
      failed(cause);
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  const done = view && ["ready_to_resume", "abandoned"].includes(view.state);
  const available =
    view && ["viewing", "awaiting_user", "human_control"].includes(view.state);
  if (agentRequested)
    return (
      <div className={styles.assistance}>
        <p role="status">
          {t(
            "The agent has requested desktop assistance. Switching to its assistance card…",
          )}
        </p>
        <ErrorNotice error={error} />
      </div>
    );
  return (
    <div
      className={styles.assistance}
      data-controlling={access?.mode === "control" && connected}
    >
      <p>
        {!callId
          ? t("Desktop control")
          : (view?.reason ?? t("Loading desktop assistance…"))}
      </p>
      {!callId && !view && mounts && mounts.length > 1 && (
        <ChoiceField
          label={t("Desktop environment")}
          value={selectedMount}
          options={mounts.map((name) => ({ value: name, label: name }))}
          onValueChange={setSelectedMount}
          disabled={busy}
        />
      )}
      {!callId && view && <p className={styles.status}>{view.mount}</p>}
      {!callId && !view && (
        <Button
          size="sm"
          disabled={busy || query.isLoading}
          onClick={() => void openDesktop()}
        >
          {t("Open desktop")}
        </Button>
      )}
      <div className={styles.status} role="status">
        {transition === "complete"
          ? t("Returning control. Revoking input access…")
          : !view && !callId
            ? t("Viewing does not pause the agent. Get control to interact.")
            : done
              ? !callId && view?.state === "ready_to_resume"
                ? t(
                    "Control returned. The task remains paused until continuation is accepted.",
                  )
                : view?.state === "abandoned"
                  ? t(
                      "Assistance expired or was cancelled. Review the result before continuing.",
                    )
                  : t(
                      "Assistance finished. Submit all responses to resume the agent.",
                    )
              : view?.state === "preparing"
                ? t("Pausing agent. Waiting for admitted operations to finish…")
                : view?.state === "viewing" && !connected
                  ? t(
                      "Viewing does not pause the agent. Get control to interact.",
                    )
                  : access?.mode === "control" && connected
                    ? t("You control this desktop. The agent is paused.")
                    : connected
                      ? t("Read-only desktop")
                      : view?.state === "blocked"
                        ? t("Desktop access cleanup is unconfirmed.")
                        : t("The agent is waiting for desktop assistance.")}
      </div>
      {access && (
        <div
          className={styles.screen}
          ref={screen}
          aria-label={t("Desktop viewer")}
        />
      )}
      {done && !callId && canContinue && (
        <Button
          size="sm"
          disabled={busy}
          onClick={() => void command("continue")}
        >
          {t("Continue agent")}
        </Button>
      )}
      {!done && view && (
        <>
          <div className={styles.actions}>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={busy || !available || access?.mode === "control"}
              onClick={() => void command("view")}
            >
              {t("Open read-only desktop")}
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={busy || !available || access?.mode === "control"}
              onClick={() =>
                void command(
                  !callId && view.state === "viewing" ? "request" : "control",
                )
              }
            >
              {t(callId ? "Take control" : "Get control")}
            </Button>
            {access && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={busy}
                onClick={() => void command("disconnect")}
              >
                {t(
                  callId || access.mode === "view"
                    ? "Disconnect desktop"
                    : "End control, keep paused",
                )}
              </Button>
            )}
          </div>
          <TextAreaField
            label={t("Assistance notes (optional)")}
            value={note}
            onChange={(value) => setNote(value.slice(0, 2048))}
            rows={2}
          />
          <div className={styles.actions}>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={busy || !available || view.state === "viewing"}
              onClick={() => void command("complete")}
            >
              {t(
                callId
                  ? "Finish assistance"
                  : canContinue
                    ? "Return control and continue"
                    : "Return control",
              )}
            </Button>
            <span>
              {t("Connections are revoked before the agent can continue.")}
            </span>
          </div>
        </>
      )}
      {view?.failure && (
        <p role="alert">
          {t("Desktop transition blocked")}: {view.failure}
        </p>
      )}
      <ErrorNotice error={error ?? query.error} />
    </div>
  );
}

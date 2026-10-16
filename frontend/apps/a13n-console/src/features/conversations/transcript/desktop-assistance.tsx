import { Button } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { data, type Schema } from "../../../shared/api";
import { ErrorNotice } from "../../../shared/feedback";
import { TextAreaField } from "../../../shared/forms";
import styles from "./desktop-assistance.module.css";
import type RFB from "@novnc/novnc/lib/rfb";

/** Credentials live only in this mounted viewer; never in query caches or storage. */
export function DesktopAssistance({
  runId,
  callId,
  onResolved,
}: {
  runId: string;
  callId: string;
  onResolved: (value: Schema["JsonValue"]) => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace();
  const api = client.workspace(workspace.id);
  const [sessionId] = useState(() => crypto.randomUUID());
  const [access, setAccess] = useState<Schema["Access"] | null>(null);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState<unknown>(null);
  const [connected, setConnected] = useState(false),
    [note, setNote] = useState("");
  const screen = useRef<HTMLDivElement>(null),
    rfb = useRef<RFB | null>(null);
  const current = useRef(access);
  current.current = access;
  const epoch = useRef(0);
  const mounted = useRef(true);
  const resolve = useRef(onResolved);
  resolve.current = onResolved;
  const query = useQuery({
    queryKey: ["desktop-assistance", workspace.id, runId, callId],
    queryFn: async () =>
      data(
        await api.GET("/api/v1/runs/{run_id}/desktop-assistance/{call_id}", {
          params: { path: { run_id: runId, call_id: callId } },
        }),
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
      try {
        const response = data(
          await api.POST(
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
          ),
        );
        if (!active) return;
        armExpiry(response.expires_in_seconds);
        timer = setTimeout(() => void renew(), 15000);
      } catch {
        stop("Desktop access could not be renewed. Input has been disabled.");
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

  async function command(
    action: "view" | "control" | "disconnect" | "complete",
  ) {
    if (!view || busy) return;
    setBusy(true);
    setError(null);
    // Stop local input before any transition, including failures and slow requests.
    closeLocal();
    try {
      const params = { path: { assistance_id: view.id } };
      const body = { session_id: sessionId, epoch: epoch.current };
      if (action === "view" || action === "control") {
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
      } else
        data(
          await api.POST(
            "/api/v1/desktop-assistance/{assistance_id}/disconnect",
            { params, body },
          ),
        );
      if (mounted.current) await query.refetch();
    } catch (cause) {
      if (mounted.current) setError(cause);
    } finally {
      if (mounted.current) setBusy(false);
    }
  }
  const done = view && ["ready_to_resume", "abandoned"].includes(view.state);
  const available =
    view && ["awaiting_user", "human_control"].includes(view.state);
  return (
    <div className={styles.assistance}>
      <p>{view?.reason ?? t("Loading desktop assistance…")}</p>
      <div className={styles.status} role="status">
        {done
          ? view?.state === "abandoned"
            ? t(
                "Assistance expired or was cancelled. Review the result before continuing.",
              )
            : t(
                "Assistance finished. Submit all responses to resume the agent.",
              )
          : access?.mode === "control" && connected
            ? t("You control this desktop. The agent is paused.")
            : connected
              ? t("Read-only desktop")
              : view?.state === "blocked"
                ? t("Access cleanup is unconfirmed. The agent remains paused.")
                : t("The agent is waiting for desktop assistance.")}
      </div>
      {access && (
        <div
          className={styles.screen}
          ref={screen}
          aria-label={t("Desktop viewer")}
        />
      )}
      {!done && (
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
              onClick={() => void command("control")}
            >
              {t("Take control")}
            </Button>
            {access && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={busy}
                onClick={() => void command("disconnect")}
              >
                {t("Disconnect desktop")}
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
              disabled={busy || !available}
              onClick={() => void command("complete")}
            >
              {t("Finish assistance")}
            </Button>
            <span>
              {t("Connections are revoked before the agent can continue.")}
            </span>
          </div>
        </>
      )}
      <ErrorNotice error={error ?? query.error} />
    </div>
  );
}

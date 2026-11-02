import type RFB from "@novnc/novnc/lib/rfb";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";

/** Credentials belong only to this mounted connection, never a query or mutation cache. */
export function BrowserViewer({
  id,
  generation,
  episode,
  controlExpiresAt,
}: {
  id: string;
  generation: number;
  episode: number;
  controlExpiresAt: string | null;
}) {
  const client = useClient();
  const cache = useQueryClient();
  const { workspace } = useWorkspace();
  const { t } = useTranslation();
  const screen = useRef<HTMLDivElement>(null);
  const [iframe, setIframe] = useState<string>();
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<unknown>();
  const [deadline, setDeadline] = useState(controlExpiresAt);
  const [grantDeadline, setGrantDeadline] = useState<string>();
  const [renewing, setRenewing] = useState(false);
  const renewRef = useRef<() => void>(() => {});
  useEffect(() => {
    let active = true;
    let renewalPending = false;
    let rfb: RFB | undefined;
    let expiry: ReturnType<typeof setTimeout> | undefined;
    const abort = new AbortController();
    const api = client.workspace(workspace.id);
    const params = { path: { browser_id: id } };
    function disconnect() {
      if (rfb) {
        rfb.viewOnly = true;
        rfb.disconnect();
        rfb = undefined;
      }
    }
    function fail(cause: unknown) {
      if (!active) return;
      active = false;
      clearTimeout(expiry);
      disconnect();
      setIframe(undefined);
      setConnected(false);
      setError(cause);
    }
    function arm(deadline: string) {
      setGrantDeadline(deadline);
      clearTimeout(expiry);
      expiry = setTimeout(
        () =>
          fail(
            new Error(
              t("Browser access expired. Reopen the viewer to reconnect."),
            ),
          ),
        Math.max(0, Date.parse(deadline) - Date.now()),
      );
    }
    async function readGrant() {
      const grant = data(
        await api.POST("/api/v1/browsers/{browser_id}/viewer", {
          params,
          signal: abort.signal,
        }),
      );
      if (
        grant.episode !== episode ||
        (grant.generation != null && grant.generation !== generation)
      )
        throw new Error(
          t("Browser control changed. Reopen the viewer to reconnect."),
        );
      return grant;
    }
    async function renew() {
      if (!active || renewalPending) return;
      renewalPending = true;
      try {
        setRenewing(true);
        const browser = await api
          .POST("/api/v1/browsers/{browser_id}/renew", {
            params,
            body: { generation, episode },
            signal: abort.signal,
          })
          .then(data);
        if (!active) return;
        cache.setQueryData(["browsers", workspace.id, "detail", id], browser);
        // Only the supplier's published grant extends local input, not the requested lease.
        const grant = await readGrant();
        if (!active) return;
        setDeadline(browser.control_expires_at);
        arm(grant.expires_at);
      } catch (cause) {
        fail(cause);
      } finally {
        renewalPending = false;
        if (active) setRenewing(false);
      }
    }
    renewRef.current = () => void renew();
    void (async () => {
      try {
        const grant = await readGrant();
        if (!active) return;
        arm(grant.expires_at);
        if (grant.protocol === "vnc") {
          const { default: imported } = await import("@novnc/novnc/lib/rfb");
          const value: unknown = imported;
          const Viewer = (
            typeof value === "function"
              ? value
              : (value as { default: typeof RFB }).default
          ) as typeof RFB;
          if (!active || !screen.current) return;
          rfb = new Viewer(screen.current, grant.url, {
            credentials: { password: grant.password ?? "" },
            shared: true,
            wsProtocols: ["binary", `a13n.browser.${grant.token}`],
          });
          const instance = rfb;
          instance.viewOnly = true;
          instance.scaleViewport = true;
          instance.addEventListener("connect", () => {
            if (active) {
              instance.viewOnly = false;
              setConnected(true);
            }
          });
          instance.addEventListener("disconnect", () =>
            fail(
              new Error(
                t("Browser disconnected. Reopen the viewer to reconnect."),
              ),
            ),
          );
          instance.addEventListener("securityfailure", () =>
            fail(new Error(t("Browser connection was refused."))),
          );
        } else {
          setIframe(grant.url);
          setConnected(true);
        }
      } catch (cause) {
        fail(cause);
      }
    })();
    return () => {
      active = false;
      abort.abort();
      renewRef.current = () => {};
      clearTimeout(expiry);
      disconnect();
    };
  }, [client, cache, workspace.id, id, generation, episode, t]);
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <ErrorNotice error={error} />
      {!error && !connected && (
        <p role="status" className="text-xs text-muted-foreground">
          {t("Connecting to browser…")}
        </p>
      )}
      {!error && connected && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          {deadline && (
            <span>
              {t("Control expires: {{time}}", {
                time: new Date(deadline).toLocaleString(),
              })}
            </span>
          )}
          {grantDeadline &&
            deadline &&
            Date.parse(grantDeadline) < Date.parse(deadline) && (
              <span>
                {t("Viewer access expires: {{time}}", {
                  time: new Date(grantDeadline).toLocaleString(),
                })}
              </span>
            )}
          <Button
            variant="outline"
            size="sm"
            disabled={renewing}
            onClick={() => renewRef.current()}
          >
            {t("Renew control")}
          </Button>
        </div>
      )}
      {!error &&
        (iframe ? (
          <iframe
            title={t("Browser viewer")}
            src={iframe}
            referrerPolicy="no-referrer"
            className="aspect-[8/5] w-full rounded-lg border-0"
          />
        ) : (
          <div className="relative aspect-[8/5] w-full overflow-hidden rounded-lg bg-black">
            <div
              ref={screen}
              aria-label={t("Browser viewer")}
              className="absolute inset-0"
            />
          </div>
        ))}
    </div>
  );
}

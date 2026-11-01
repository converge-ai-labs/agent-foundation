import type RFB from "@novnc/novnc/lib/rfb";
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
}: {
  id: string;
  generation: number;
  episode: number;
}) {
  const client = useClient();
  const { workspace } = useWorkspace();
  const { t } = useTranslation();
  const screen = useRef<HTMLDivElement>(null);
  const [iframe, setIframe] = useState<string>();
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<unknown>();
  useEffect(() => {
    let active = true;
    let rfb: RFB | undefined;
    let renewal: ReturnType<typeof setTimeout> | undefined;
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
      clearTimeout(renewal);
      clearTimeout(expiry);
      disconnect();
      setIframe(undefined);
      setConnected(false);
      setError(cause);
    }
    function arm(deadline: string) {
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
      if (!active) return;
      try {
        await api
          .POST("/api/v1/browsers/{browser_id}/renew", {
            params,
            body: { generation, episode },
            signal: abort.signal,
          })
          .then(data);
        // Only the supplier's published grant extends local input, not the requested lease.
        const grant = await readGrant();
        if (!active) return;
        arm(grant.expires_at);
        renewal = setTimeout(() => void renew(), 15000);
      } catch (cause) {
        fail(cause);
      }
    }
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
        renewal = setTimeout(() => void renew(), 15000);
      } catch (cause) {
        fail(cause);
      }
    })();
    return () => {
      active = false;
      abort.abort();
      clearTimeout(renewal);
      clearTimeout(expiry);
      disconnect();
    };
  }, [client, workspace.id, id, generation, episode, t]);
  return (
    <div className="flex flex-col gap-3">
      <ErrorNotice error={error} />
      {!error && (
        <p role="status" className="text-xs text-muted-foreground">
          {t(
            connected
              ? "You control this browser. Return control when finished."
              : "Connecting to browser…",
          )}
        </p>
      )}
      {!error &&
        (iframe ? (
          <iframe
            title={t("Browser viewer")}
            src={iframe}
            referrerPolicy="no-referrer"
            className="h-[min(52vh,520px)] w-full rounded-lg border-0"
          />
        ) : (
          <div
            ref={screen}
            aria-label={t("Browser viewer")}
            className="aspect-[8/5] w-full overflow-hidden rounded-lg bg-black sm:aspect-auto sm:h-[min(52vh,520px)]"
          />
        ))}
    </div>
  );
}

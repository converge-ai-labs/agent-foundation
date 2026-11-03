import { Button, ChoiceField } from "a13n-ui";
import { MonitorIcon, XIcon } from "@phosphor-icons/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useEffectEvent, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type RFB from "@novnc/novnc/lib/rfb";
import { revalidateSession, useAuth, useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import {
  data,
  ifMatch,
  representation,
  type Schema,
} from "../../../shared/api";
import { ErrorNotice, Loading, Timestamp } from "../../../shared/feedback";
import { conversationQueries } from "../api";
import styles from "./environment-desktop.module.css";

/** The panel belongs to the Thread, independently of Run status. */
export function ThreadDesktopPanel({
  threadId,
  onClose,
}: {
  threadId: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-border px-4">
        <MonitorIcon size={16} aria-hidden="true" />
        <h2 className="flex-1 text-sm font-medium">{t("Desktop")}</h2>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t("Close desktop panel")}
          onClick={onClose}
        >
          <XIcon size={16} />
        </Button>
      </div>
      <div className="a13n-scrollbar min-h-0 flex-1 overflow-y-auto p-4">
        <DesktopMounts threadId={threadId} />
      </div>
    </div>
  );
}

function DesktopMounts({ threadId }: { threadId: string }) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation();
  const actorId = useAuth().data?.user.value.id;
  const mounts = useQuery({
    ...conversationQueries(client, workspace.id).environments(threadId),
    refetchInterval: 3000,
  });
  const [selected, setSelected] = useState<string>();
  const candidates = mounts.data?.filter((item) => item.desktop_enabled) ?? [];
  const mount =
    candidates.find((item) => item.name === selected) ?? candidates[0];
  return (
    <div className={styles.panel}>
      <ErrorNotice error={mounts.error} retry={() => void mounts.refetch()} />
      {mounts.isPending ? (
        <Loading variant="form" />
      ) : !mount ? (
        !mounts.isError && (
          <p>{t("No desktop-enabled environments mounted.")}</p>
        )
      ) : (
        <>
          <ChoiceField
            label={t("Environment mount")}
            value={mount.name}
            options={candidates.map((item) => ({
              value: item.name,
              label: item.name,
            }))}
            onValueChange={setSelected}
          />
          {actorId && !mounts.isError && (
            <EnvironmentDesktop
              key={`${workspace.id}:${threadId}:${mount.environment_id}:${actorId}`}
              environmentId={mount.environment_id}
              threadId={threadId}
              actorId={actorId}
            />
          )}
        </>
      )}
    </div>
  );
}

type Control = Schema["Control"];
type Viewer =
  Schema["a13n_service__runs__environments__desktop__schemas__Viewer"];

export function permitsViewer(
  control: Control,
  viewer: Viewer,
  actorId: string,
  now = Date.now(),
) {
  return (
    viewer.generation === control.generation &&
    viewer.episode === control.episode &&
    Date.parse(viewer.expires_at) > now &&
    (viewer.mode === "view" ||
      (control.control === "human" &&
        control.controller_id === actorId &&
        !!control.expires_at &&
        Date.parse(control.expires_at) > now))
  );
}

/** Only control metadata is cached. Viewer credentials and connections die with this panel. */
export function EnvironmentDesktop({
  environmentId,
  threadId,
  actorId,
}: {
  environmentId: string;
  threadId: string;
  actorId: string;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace();
  const cache = useQueryClient();
  const api = client.workspace(workspace.id);
  const key = ["desktop-control", workspace.id, environmentId];
  const path = { environment_id: environmentId };
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>();
  const [viewer, setViewer] = useState<Viewer | null>(null);
  const [connected, setConnected] = useState(false);
  const [opening, setOpening] = useState(false);
  const controlIntent = useRef<{ generation: number; episode: number } | null>(
    null,
  );
  const openingAuthority = useRef<{
    control: Control;
    mode: Viewer["mode"];
  } | null>(null);
  const screen = useRef<HTMLDivElement>(null),
    rfb = useRef<RFB | null>(null);
  const epoch = useRef(0),
    alive = useRef(false);
  const query = useQuery({
    queryKey: key,
    queryFn: async ({ signal }) =>
      representation(
        await api.GET("/api/v1/environments/{environment_id}/desktop-control", {
          params: { path },
          signal,
        }),
      ),
    refetchInterval: 2000,
    enabled: !busy,
    staleTime: 0,
  });
  const control = query.data?.value;
  const authority = useRef(control);
  authority.current = query.isError ? undefined : control;
  const currentViewer = useRef(viewer);
  currentViewer.current = viewer;
  const actions = useRef({
    close: () => {},
    open: async (_mode: Viewer["mode"]) => {},
  });
  function closeLocal() {
    epoch.current++;
    openingAuthority.current = null;
    controlIntent.current = null;
    setOpening(false);
    const instance = rfb.current;
    rfb.current = null;
    if (instance) {
      instance.viewOnly = true;
      instance.disconnect();
    }
    currentViewer.current = null;
    setViewer(null);
    setConnected(false);
  }
  function fail(cause: unknown) {
    closeLocal();
    setError(cause);
    revalidateSession(cause);
  }
  const connectionFailed = useEffectEvent(
    (reason: "disconnected" | "refused" | "expired") => {
      fail(
        new Error(
          t(
            reason === "disconnected"
              ? "Desktop disconnected. Open it again to reconnect."
              : reason === "refused"
                ? "Desktop connection was refused."
                : "Desktop access expired. Open it again to reconnect.",
          ),
        ),
      );
    },
  );
  async function openViewer(mode: Viewer["mode"]) {
    const initial = authority.current;
    if (openingAuthority.current || !initial) return;
    openingAuthority.current = { control: initial, mode };
    setOpening(true);
    const ticket = epoch.current;
    try {
      const grant = data(
        await api.POST("/api/v1/environments/{environment_id}/desktop-viewer", {
          params: { path },
          body: {
            mode,
            generation: initial.generation,
            episode: initial.episode,
            viewer_id:
              mode === "view" ? currentViewer.current?.viewer_id : undefined,
          },
        }),
      );
      if (!alive.current || ticket !== epoch.current) return;
      if (
        !authority.current ||
        !permitsViewer(initial, grant, actorId) ||
        !permitsViewer(authority.current, grant, actorId)
      )
        throw new Error(
          t("Desktop authority changed. Refresh before reconnecting."),
        );
      setViewer(grant);
    } catch (cause) {
      if (alive.current && ticket === epoch.current) fail(cause);
    } finally {
      if (alive.current && ticket === epoch.current) {
        openingAuthority.current = null;
        setOpening(false);
      }
    }
  }
  actions.current = { close: closeLocal, open: openViewer };
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      actions.current.close();
    };
  }, []);

  // Invalidate pending Connect intent too, including return/retake in another tab.
  useEffect(() => {
    const intent = controlIntent.current;
    if (
      intent &&
      (!control ||
        query.isError ||
        control.generation !== intent.generation ||
        control.episode !== intent.episode ||
        control.controller_id !== actorId ||
        !["draining", "human"].includes(control.control))
    )
      controlIntent.current = null;
    const pending = openingAuthority.current;
    const pendingChanged =
      pending &&
      (!control ||
        control.generation !== pending.control.generation ||
        control.episode !== pending.control.episode ||
        (pending.mode === "control" &&
          (control.control !== "human" || control.controller_id !== actorId)));
    if (
      query.isError ||
      pendingChanged ||
      (viewer && (!control || !permitsViewer(control, viewer, actorId)))
    )
      actions.current.close();
  }, [control, viewer, actorId, query.isError]);

  // Opening the panel starts observation. Only this panel's explicit Take control
  // intent may automatically upgrade a connection after authorization completes.
  useEffect(() => {
    if (!control || busy || query.isError || error || openingAuthority.current)
      return;
    const intent = controlIntent.current;
    const controlling =
      intent &&
      control.control === "human" &&
      control.controller_id === actorId &&
      !!control.expires_at &&
      Date.parse(control.expires_at) > Date.now();
    if (controlling && viewer?.mode !== "control") {
      actions.current.close();
      void actions.current.open("control");
    } else if (!viewer && !(intent && control.control === "draining")) {
      void actions.current.open("view");
    }
  }, [control, busy, query.isError, error, viewer, actorId]);

  const url = viewer?.websocket_url,
    password = viewer?.password;
  const generation = viewer?.generation,
    episode = viewer?.episode,
    mode = viewer?.mode;
  useEffect(() => {
    if (!url || !password) return;
    let active = true;
    let instance: RFB | undefined;
    setConnected(false);
    void import("@novnc/novnc/lib/rfb")
      .then(({ default: imported }) => {
        const value: unknown = imported;
        const ViewerClass = (
          typeof value === "function"
            ? value
            : (value as { default: typeof RFB }).default
        ) as typeof RFB;
        if (!active || !screen.current) return;
        instance = new ViewerClass(screen.current, url, {
          credentials: { password },
          shared: true,
        });
        rfb.current = instance;
        instance.viewOnly = true;
        instance.scaleViewport = true;
        instance.addEventListener("connect", () => {
          if (!active || !instance) return;
          const current = authority.current;
          const grant = currentViewer.current;
          if (!current || !grant || !permitsViewer(current, grant, actorId)) {
            actions.current.close();
            return;
          }
          instance.viewOnly = mode !== "control";
          setConnected(true);
        });
        const refused = () => {
          if (!active || rfb.current !== instance) return;
          active = false;
          connectionFailed("disconnected");
        };
        instance.addEventListener("disconnect", refused);
        instance.addEventListener("securityfailure", refused);
      })
      .catch(() => {
        if (!active) return;
        connectionFailed("refused");
      });
    return () => {
      active = false;
      if (instance) {
        instance.viewOnly = true;
        instance.disconnect();
        if (rfb.current === instance) rfb.current = null;
      }
    };
  }, [url, password, generation, episode, mode, actorId]);

  useEffect(() => {
    if (!viewer) return;
    const deadline = Math.min(
      Date.parse(viewer.expires_at),
      viewer.mode === "control" && control?.expires_at
        ? Date.parse(control.expires_at)
        : Infinity,
    );
    const expiry = setTimeout(
      () => connectionFailed("expired"),
      Math.max(0, deadline - Date.now()),
    );
    // Refresh the short viewer grant separately from the user's explicit control lease.
    // A grant capped by the human lease cannot gain time until explicit renewal.
    const refresh =
      viewer.mode === "control" &&
      control?.expires_at &&
      Date.parse(viewer.expires_at) >= Date.parse(control.expires_at)
        ? undefined
        : setTimeout(
            () => void actions.current.open(viewer.mode),
            Math.max(1000, deadline - Date.now() - 15000),
          );
    return () => {
      clearTimeout(expiry);
      clearTimeout(refresh);
    };
  }, [viewer, control?.expires_at]);

  async function command(kind: "takeover" | "renew" | "handback") {
    if (busy || !query.data) return;
    if (kind !== "renew") closeLocal();
    setBusy(true);
    setError(undefined);
    await cache.cancelQueries({ queryKey: key });
    try {
      const params = { path, header: ifMatch(query.data.etag) };
      const result =
        kind === "takeover"
          ? await api.POST(
              "/api/v1/environments/{environment_id}/desktop-control/takeover",
              { params, body: { thread_id: threadId } },
            )
          : kind === "handback"
            ? await api.POST(
                "/api/v1/environments/{environment_id}/desktop-control/handback",
                { params },
              )
            : await api.POST(
                "/api/v1/environments/{environment_id}/desktop-control/renew",
                {
                  params: { path },
                  body: {
                    generation: query.data.value.generation,
                    episode: query.data.value.episode,
                  },
                },
              );
      const updated = representation(result);
      if (!alive.current) return;
      if (kind === "takeover")
        controlIntent.current = {
          generation: updated.value.generation,
          episode: updated.value.episode,
        };
      cache.setQueryData(key, updated);
    } catch (cause) {
      if (alive.current) fail(cause);
    } finally {
      if (alive.current) {
        setBusy(false);
        void query.refetch();
      }
    }
  }
  const mine = control?.controller_id === actorId;
  const human =
    mine &&
    control?.control === "human" &&
    !!control.expires_at &&
    Date.parse(control.expires_at) > Date.now();
  return (
    <div className={styles.panel}>
      <ErrorNotice
        error={error ?? query.error}
        retry={() => {
          setError(undefined);
          void query.refetch();
        }}
      />
      {!control ? (
        query.isPending && <Loading variant="form" />
      ) : (
        <>
          <div className={styles.status} role="status">
            <strong>
              {t(
                control.control === "automation"
                  ? "Agent controls Computer"
                  : control.control === "draining"
                    ? "Authorizing Computer control"
                    : control.control === "returning"
                      ? "Returning Computer control"
                      : mine
                        ? "You control Computer"
                        : "Another user controls Computer",
              )}
            </strong>
            {control.expires_at && (
              <span>
                {t("Control expires")} <Timestamp value={control.expires_at} />
              </span>
            )}
          </div>
          {control.failure && (
            <p role="alert">
              {t(
                control.control === "automation"
                  ? "Could not take control. You can try again."
                  : "Control cleanup is incomplete. Retrying automatically.",
              )}
            </p>
          )}
          <div className={styles.actions}>
            {control.control === "automation" && (
              <Button
                size="sm"
                disabled={busy || query.isError}
                loading={busy}
                onClick={() => void command("takeover")}
              >
                {t("Take control")}
              </Button>
            )}
            {human && (
              <>
                {viewer?.mode !== "control" && (
                  <Button
                    size="sm"
                    disabled={busy || opening || query.isError}
                    onClick={() => {
                      closeLocal();
                      controlIntent.current = {
                        generation: control.generation,
                        episode: control.episode,
                      };
                      setError(undefined);
                      void openViewer("control");
                    }}
                  >
                    {t("Take control")}
                  </Button>
                )}
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy || query.isError}
                  onClick={() => void command("renew")}
                >
                  {t("Renew control")}
                </Button>
              </>
            )}
            {mine && control.control !== "automation" && (
              <Button
                size="sm"
                variant="outline"
                disabled={busy || query.isError}
                loading={busy}
                onClick={() => void command("handback")}
              >
                {t("Return control")}
              </Button>
            )}
          </div>
          <p className={styles.hint}>
            {t(
              "The Agent can continue other work. Closing this panel does not return control.",
            )}
          </p>
        </>
      )}
      <div
        ref={screen}
        className={styles.screen}
        hidden={!viewer}
        aria-label={t("Live desktop")}
      />
      {viewer && (
        <span className={styles.hint}>
          {t(
            !connected
              ? "Connecting…"
              : viewer.mode === "control"
                ? "Input enabled"
                : "Read-only viewer",
          )}
        </span>
      )}
    </div>
  );
}

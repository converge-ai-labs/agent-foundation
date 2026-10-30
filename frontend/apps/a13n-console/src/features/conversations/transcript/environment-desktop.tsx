import { Button, ChoiceField, ModalFrame } from "a13n-ui";
import { MonitorIcon } from "@phosphor-icons/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
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

/** The entry point belongs to the Thread, not to a Run or a pending tool call. */
export function ThreadDesktop({ threadId }: { threadId: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <ModalFrame
      open={open}
      onOpenChange={setOpen}
      title={t("Environment desktop")}
      description={t(
        "Take control of Computer input while the Agent continues its other work.",
      )}
      closeLabel={t("Close")}
      size="xl"
      trigger={
        <Button className={styles.trigger} size="sm" variant="outline">
          <MonitorIcon size={14} />
          {t("Desktop")}
        </Button>
      }
    >
      {open && <DesktopMounts threadId={threadId} />}
    </ModalFrame>
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
  const mount =
    mounts.data?.find((item) => item.name === selected) ?? mounts.data?.[0];
  return (
    <div className={styles.panel}>
      <ErrorNotice error={mounts.error} retry={() => void mounts.refetch()} />
      {mounts.isPending ? (
        <Loading variant="form" />
      ) : !mount ? (
        <p>{t("No environments mounted.")}</p>
      ) : (
        <>
          <ChoiceField
            label={t("Environment mount")}
            value={mount.name}
            options={mounts.data!.map((item) => ({
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
  const openingRef = useRef(false);
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
    openingRef.current = false;
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
  async function openViewer(mode: Viewer["mode"]) {
    if (openingRef.current) return;
    openingRef.current = true;
    setOpening(true);
    const ticket = epoch.current;
    try {
      const grant = data(
        await api.POST("/api/v1/environments/{environment_id}/desktop-viewer", {
          params: { path },
          body: { mode },
        }),
      );
      if (!alive.current || ticket !== epoch.current) return;
      if (
        !authority.current ||
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
        openingRef.current = false;
        setOpening(false);
      }
    }
  }
  actions.current = { close: closeLocal, open: openViewer };
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      epoch.current++;
      const instance = rfb.current;
      rfb.current = null;
      if (instance) {
        instance.viewOnly = true;
        instance.disconnect();
      }
    };
  }, []);

  // Revoke local input immediately when metadata invalidates its generation or lease.
  useEffect(() => {
    if (
      viewer &&
      (query.isError || !control || !permitsViewer(control, viewer, actorId))
    )
      actions.current.close();
  }, [control, viewer, actorId, query.isError]);

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
          actions.current.close();
          setError(
            new Error(t("Desktop disconnected. Open it again to reconnect.")),
          );
        };
        instance.addEventListener("disconnect", refused);
        instance.addEventListener("securityfailure", refused);
      })
      .catch(() => {
        if (!active) return;
        actions.current.close();
        setError(new Error(t("Desktop connection was refused.")));
      });
    return () => {
      active = false;
      if (instance) {
        instance.viewOnly = true;
        instance.disconnect();
        if (rfb.current === instance) rfb.current = null;
      }
    };
  }, [url, password, generation, episode, mode, actorId, t]);

  useEffect(() => {
    if (!viewer) return;
    const deadline = Math.min(
      Date.parse(viewer.expires_at),
      viewer.mode === "control" && control?.expires_at
        ? Date.parse(control.expires_at)
        : Infinity,
    );
    const expiry = setTimeout(
      () => {
        actions.current.close();
        setError(
          new Error(t("Desktop access expired. Open it again to reconnect.")),
        );
      },
      Math.max(0, deadline - Date.now()),
    );
    // Refresh the short viewer grant separately from the user's explicit control lease.
    const refresh = setTimeout(
      () => void actions.current.open(viewer.mode),
      Math.max(1000, deadline - Date.now() - 15000),
    );
    return () => {
      clearTimeout(expiry);
      clearTimeout(refresh);
    };
  }, [viewer, control?.expires_at, t]);

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
                    ? "Waiting for Computer actions to settle"
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
            <p>
              {t(
                "Control transition needs attention. Refresh or return control to retry cleanup.",
              )}
            </p>
          )}
          <div className={styles.actions}>
            <Button
              size="sm"
              variant="outline"
              disabled={busy || opening || !!viewer || query.isError}
              onClick={() => {
                setError(undefined);
                void openViewer("view");
              }}
            >
              {t("View desktop")}
            </Button>
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
                <Button
                  size="sm"
                  disabled={
                    busy ||
                    opening ||
                    viewer?.mode === "control" ||
                    query.isError
                  }
                  onClick={() => {
                    closeLocal();
                    setError(undefined);
                    void openViewer("control");
                  }}
                >
                  {t("Connect input")}
                </Button>
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
            {(viewer || opening) && (
              <Button size="sm" variant="ghost" onClick={closeLocal}>
                {t("Close viewer")}
              </Button>
            )}
          </div>
          <p className={styles.hint}>
            {t(
              "Closing the viewer leaves control unchanged. Return control explicitly, or let its lease expire. Runs are never paused or resumed here.",
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

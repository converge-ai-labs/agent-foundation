import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { ArrowClockwise, Hand, LockSimple, Stop } from "@phosphor-icons/react";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { bindClipboard } from "./clipboard";
import { bindKeyboard } from "./keyboard";
import { BrowserSetup } from "./setup";
import styles from "./panel.module.css";

export function BrowserPanel({
  threadId,
  fullControl,
}: {
  threadId?: string;
  fullControl: boolean;
}) {
  const { client } = useTransport();
  const queries = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [controlToken, setControlToken] = useState("");
  const state = useQuery({
    queryKey: ["browser", threadId],
    enabled: !!threadId && fullControl,
    queryFn: () =>
      result(
        client.GET("/api/threads/{thread_id}/browser", {
          params: { path: { thread_id: threadId! } },
        }),
      ),
    refetchInterval: 2000,
    retry: false,
  });
  const session = state.data;
  const preparation = useQuery({
    queryKey: ["browser-setup", threadId],
    enabled:
      !!threadId &&
      fullControl &&
      state.isSuccess &&
      session?.status !== "ready",
    queryFn: () =>
      result(
        client.GET("/api/threads/{thread_id}/browser/setup", {
          params: { path: { thread_id: threadId! } },
        }),
      ),
    refetchOnWindowFocus: false,
    refetchInterval: (query) =>
      query.state.data?.installation?.status === "installing" ? 1000 : false,
    retry: false,
  });
  const install = async () => {
    if (!threadId || busy) return;
    setBusy(true);
    setError("");
    try {
      const value = await result(
        client.POST("/api/threads/{thread_id}/browser/install", {
          params: { path: { thread_id: threadId } },
        }),
      );
      queries.setQueryData(["browser-setup", threadId], value);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  };
  const refresh = () =>
    queries.invalidateQueries({ queryKey: ["browser", threadId] });
  const command = async (
    kind: "enable" | "start" | "reset" | "stop" | "take-control" | "handback",
  ) => {
    if (!threadId || busy) return;
    setBusy(true);
    setError("");
    try {
      const path = {
        params: { path: { thread_id: threadId } },
        body: { session_id: session?.session_id ?? "" },
      };
      if (kind === "enable")
        await result(
          client.POST("/api/threads/{thread_id}/browser/enable", {
            params: path.params,
          }),
        );
      if (kind === "start")
        await result(
          client.POST("/api/threads/{thread_id}/browser/start", path),
        );
      if (kind === "reset") {
        await result(
          client.POST("/api/threads/{thread_id}/browser/reset", path),
        );
        setControlToken("");
      }
      if (kind === "stop") {
        await result(
          client.POST("/api/threads/{thread_id}/browser/stop", path),
        );
        setControlToken("");
      }
      if (kind === "take-control") {
        const value = await result(
          client.POST("/api/threads/{thread_id}/browser/take-control", path),
        );
        queries.setQueryData(["browser", threadId], value.state);
        setControlToken(value.control_token);
      }
      if (kind === "handback") {
        const value = await result(
          client.POST("/api/threads/{thread_id}/browser/handback", path),
        );
        queries.setQueryData(["browser", threadId], value.state);
        setControlToken("");
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
      if (kind === "enable" || kind === "start" || kind === "reset")
        await preparation.refetch();
    } finally {
      await refresh();
      setBusy(false);
    }
  };
  const changeViewport = async (
    preset: Schema<"BrowserViewportRequest">["preset"],
  ) => {
    if (!threadId || !session?.session_id || !controlToken || busy) return;
    setBusy(true);
    setError("");
    try {
      const value = await result(
        client.POST("/api/threads/{thread_id}/browser/viewport", {
          params: { path: { thread_id: threadId } },
          body: {
            session_id: session.session_id,
            control_token: controlToken,
            preset,
          },
        }),
      );
      queries.setQueryData(["browser", threadId], value);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      await refresh();
      setBusy(false);
    }
  };
  useEffect(() => {
    if (session?.control !== "human") setControlToken("");
  }, [session?.control, session?.session_id]);
  if (!threadId || !fullControl)
    return (
      <div className={styles.empty}>
        <LockSimple size={28} />
        <strong>Browse on the Host</strong>
        <p>Open a Full Control conversation to enable its browser.</p>
      </div>
    );
  const ready = session?.status === "ready";
  const stopped = session?.status === "stopped" || session?.status === "failed";
  const human = session?.control === "human";
  return (
    <section className={styles.panel} aria-label="Host browser">
      {ready && (
        <div className={styles.toolbar}>
          <select
            className={styles.viewport}
            aria-label="Browser viewport size"
            title={
              controlToken
                ? "Resize the page viewport"
                : "Take control to change the viewport size"
            }
            disabled={busy || !human || !controlToken}
            value={session.viewport}
            onChange={(event) =>
              void changeViewport(
                event.target
                  .value as Schema<"BrowserViewportRequest">["preset"],
              )
            }
          >
            {(session.viewports ?? []).map((viewport) => (
              <option key={viewport.preset} value={viewport.preset}>
                {viewport.label} · {viewport.width} × {viewport.height}
              </option>
            ))}
          </select>
          <Button
            size="sm"
            variant={human ? "default" : "outline"}
            disabled={busy}
            onClick={() => void command(human ? "handback" : "take-control")}
          >
            <Hand />
            {human ? "Hand back to Agent" : "Take control"}
          </Button>
          <Button
            size="icon"
            variant="ghost"
            aria-label="Reset browser"
            title="Restart browser and restore window position"
            disabled={busy}
            onClick={() => void command("reset")}
          >
            <ArrowClockwise />
          </Button>
          <Button
            size="icon"
            variant="ghost"
            aria-label="Stop browser"
            disabled={busy}
            onClick={() => void command("stop")}
          >
            <Stop />
          </Button>
        </div>
      )}
      {(error || state.error || session?.error) && (
        <p className={styles.notice} role="alert">
          {error || state.error?.message || session?.error}
        </p>
      )}
      {ready ? (
        <Desktop
          key={session.session_id}
          threadId={threadId}
          session={session}
          controlToken={human ? controlToken : ""}
          busy={busy}
        />
      ) : !preparation.data?.ready ? (
        <BrowserSetup
          setup={preparation.data}
          loading={preparation.isFetching || state.isLoading}
          error={preparation.error}
          check={() => void preparation.refetch()}
          install={() => void install()}
          busy={busy}
        />
      ) : (
        <div className={styles.empty}>
          <LockSimple size={28} />
          <strong>
            {stopped ? "Browser stopped" : "Your browser on the Host"}
          </strong>
          <p>
            {stopped
              ? "Start again with your saved browser profile."
              : "A dedicated browser with saved login state. Take control for login and verification, then let the Agent continue."}
          </p>
          <Button
            disabled={busy || state.isLoading || preparation.isFetching}
            onClick={() =>
              void command(session?.session_id ? "start" : "enable")
            }
          >
            {busy
              ? "Preparing…"
              : session?.session_id
                ? "Start browser"
                : "Enable browser"}
          </Button>
          {session?.status === "enabled" && (
            <Button
              variant="ghost"
              disabled={busy}
              onClick={() => void command("stop")}
            >
              Stop browser
            </Button>
          )}
          <p className={styles.footnote}>
            Browser support is installed on this Host.
          </p>
        </div>
      )}
    </section>
  );
}

function Desktop({
  threadId,
  session,
  controlToken,
  busy,
}: {
  threadId: string;
  session: Schema<"BrowserState">;
  controlToken: string;
  busy: boolean;
}) {
  const { client } = useTransport();
  const host = useRef<HTMLDivElement>(null);
  const snapshot = useRef<HTMLCanvasElement>(null);
  const rfb = useRef<import("@novnc/novnc").default | null>(null);
  const [status, setStatus] = useState("Connecting…");
  const [clipboardError, setClipboardError] = useState("");
  const clipboard = useRef<ReturnType<typeof bindClipboard> | null>(null);
  const inputAllowed = useRef(false);
  const [reconnect, setReconnect] = useState(0);
  const [canReconnect, setCanReconnect] = useState(false);
  const [canControl, setCanControl] = useState(false);
  const [connected, setConnected] = useState(false);
  const [showControlHint, setShowControlHint] = useState(false);
  const interactive = canControl && !busy;
  inputAllowed.current = interactive && !!controlToken;
  useEffect(() => {
    if (interactive) {
      rfb.current?.focus();
      clipboard.current?.sync();
    }
  }, [interactive]);
  useEffect(() => {
    let disposed = false;
    let bridge: ReturnType<typeof bindClipboard> | undefined;
    let keyboard: ReturnType<typeof bindKeyboard> | undefined;
    let frame = 0;
    const retain = () => {
      const saved = snapshot.current;
      if (!saved) return;
      try {
        const pixels = rfb.current?.getImageData?.();
        if (!pixels?.width || !pixels.height || pixels.data[3] !== 255) return;
        saved.width = pixels.width;
        saved.height = pixels.height;
        saved.getContext("2d")?.putImageData(pixels, 0, 0);
        saved.hidden = false;
      } catch {
        // A connection that never rendered has no frame to retain.
      }
    };
    setStatus(snapshot.current?.hidden === false ? "" : "Connecting…");
    setConnected(false);
    setCanReconnect(false);
    setCanControl(false);
    setShowControlHint(false);
    setClipboardError("");
    void (async () => {
      try {
        const { default: RFB } = await import("@novnc/novnc");
        if (disposed) return;
        const access = await result(
          client.POST("/api/threads/{thread_id}/browser/access", {
            params: { path: { thread_id: threadId } },
            body: {
              session_id: session.session_id!,
              control_token: controlToken,
            },
          }),
        );
        if (disposed) return;
        const url = new URL(
          `/api/threads/${encodeURIComponent(threadId)}/browser/connect`,
          window.location.origin,
        );
        url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
        url.searchParams.set("ticket", access.ticket);
        const connection = new RFB(host.current!, url.toString(), {
          credentials: { password: access.password },
        });
        rfb.current = connection;
        connection.viewOnly = !access.control;
        connection.scaleViewport = true;
        connection.addEventListener("connect", () => {
          if (!disposed) {
            setShowControlHint(false);
            setStatus("");
            const reveal = () => {
              if (disposed) return;
              const saved = snapshot.current;
              const canvas = host.current?.querySelector("canvas");
              // Keep the last frame until noVNC paints the replacement connection.
              if (
                saved &&
                !saved.hidden &&
                canvas &&
                canvas.getContext("2d")?.getImageData(0, 0, 1, 1).data[3] !==
                  255
              ) {
                frame = requestAnimationFrame(reveal);
                return;
              }
              if (saved) {
                saved.hidden = true;
                saved.width = saved.height = 0;
              }
              setConnected(true);
              setCanControl(access.control);
            };
            reveal();
            if (access.control) {
              const active = () =>
                inputAllowed.current && rfb.current === connection;
              keyboard = bindKeyboard({
                root: host.current!,
                active,
                connection,
              });
              bridge = bindClipboard({
                root: host.current!,
                active,
                connection,
                shortcut: keyboard.shortcut,
                notice: setClipboardError,
              });
              clipboard.current = bridge;
            }
          }
        });
        connection.addEventListener("disconnect", () => {
          if (!disposed) {
            retain();
            cancelAnimationFrame(frame);
            setStatus("Disconnected · Reconnect to view the current desktop");
            bridge?.stop();
            keyboard?.stop();
            inputAllowed.current = false;
            setCanReconnect(true);
            setConnected(false);
            setCanControl(false);
          }
        });
        connection.addEventListener("securityfailure", () => {
          if (!disposed) {
            retain();
            cancelAnimationFrame(frame);
            setStatus("Connection rejected · Reconnect to request new access");
            bridge?.stop();
            keyboard?.stop();
            inputAllowed.current = false;
            setCanReconnect(true);
            setConnected(false);
            setCanControl(false);
          }
        });
      } catch (cause) {
        if (!disposed) {
          setStatus(cause instanceof Error ? cause.message : String(cause));
          setCanReconnect(true);
        }
      }
    })();
    return () => {
      retain();
      disposed = true;
      cancelAnimationFrame(frame);
      inputAllowed.current = false;
      bridge?.stop();
      keyboard?.stop();
      if (clipboard.current === bridge) clipboard.current = null;
      rfb.current?.disconnect();
      rfb.current = null;
    };
  }, [
    client,
    threadId,
    session.session_id,
    session.control,
    controlToken,
    reconnect,
  ]);
  return (
    <>
      {!!status && (
        <div className={styles.connection}>
          <span role="status">{status}</span>
          {canReconnect && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setReconnect((v) => v + 1)}
            >
              Reconnect viewer
            </Button>
          )}
        </div>
      )}
      {!!clipboardError && (
        <p className={styles.notice} role="status">
          {clipboardError}
        </p>
      )}
      <div
        className={styles.desktop}
        aria-label="Remote browser desktop"
        aria-busy={!connected || busy}
      >
        <div
          ref={host}
          data-browser-screen
          className={styles.screen}
          inert={!interactive}
        />
        <canvas
          ref={snapshot}
          className={styles.snapshot}
          data-browser-frame="retained"
          aria-hidden="true"
          hidden
        />
        {!interactive && (
          <button
            type="button"
            className={styles.readOnlyGuard}
            aria-label="Read-only browser"
            onPointerDown={() => setShowControlHint(true)}
            onClick={() => setShowControlHint(true)}
            onContextMenu={(event) => {
              event.preventDefault();
              setShowControlHint(true);
            }}
          />
        )}
        {showControlHint && !interactive && (
          <div className={styles.controlHint} role="status">
            <LockSimple size={18} aria-hidden="true" />
            <span>
              {busy
                ? "Updating the browser. Please wait."
                : controlToken
                  ? "Input is unavailable. Wait for the connection or reconnect the viewer."
                  : session.control === "human"
                    ? "Read only. Hand back to Agent, then select Take control to interact here."
                    : "Read only. Select Take control to interact with this browser."}
            </span>
          </div>
        )}
      </div>
    </>
  );
}

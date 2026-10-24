import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "a13n-ui";
import {
  ArrowClockwise,
  HandArrowDown,
  HandArrowUp,
} from "@phosphor-icons/react";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { bindClipboard } from "./clipboard";
import { bindKeyboard } from "./keyboard";
import { bindTextInput } from "./text-input";
import styles from "./panel.module.css";

export type BrowserScale = "fit" | "50" | "75" | "100" | "125" | "150" | "200";

export function Desktop({
  threadId,
  session,
  controlToken,
  busy,
  scale = "fit",
}: {
  threadId: string;
  session: Schema<"BrowserState">;
  controlToken: string;
  busy: boolean;
  scale?: BrowserScale;
}) {
  const { client } = useTransport();
  const host = useRef<HTMLDivElement>(null);
  const snapshot = useRef<HTMLCanvasElement>(null);
  const rfb = useRef<import("@novnc/novnc").default | null>(null);
  const [status, setStatus] = useState("Connecting…");
  const [clipboardError, setClipboardError] = useState("");
  const clipboard = useRef<ReturnType<typeof bindClipboard> | null>(null);
  const textInput = useRef<ReturnType<typeof bindTextInput> | null>(null);
  const inputAllowed = useRef(false);
  const [reconnect, setReconnect] = useState(0);
  const [canReconnect, setCanReconnect] = useState(false);
  const [canControl, setCanControl] = useState(false);
  const [connected, setConnected] = useState(false);
  const [showControlHint, setShowControlHint] = useState(false);
  const [size, setSize] = useState({ width: 1280, height: 800 });
  const hintTimeout = useRef<ReturnType<typeof setTimeout> | null>(null);
  const showHint = useCallback(() => {
    if (hintTimeout.current !== null) clearTimeout(hintTimeout.current);
    setShowControlHint(true);
    hintTimeout.current = setTimeout(() => setShowControlHint(false), 3000);
  }, []);
  useEffect(
    () => () => {
      if (hintTimeout.current !== null) clearTimeout(hintTimeout.current);
    },
    [],
  );
  const zoom = scale === "fit" ? 1 : Number(scale) / 100;
  const interactive = canControl && !busy;
  inputAllowed.current = interactive && !!controlToken;
  useEffect(() => {
    if (interactive) {
      textInput.current?.focus();
      clipboard.current?.sync();
    }
  }, [interactive]);
  useEffect(() => {
    let disposed = false;
    let bridge: ReturnType<typeof bindClipboard> | undefined;
    let keyboard: ReturnType<typeof bindKeyboard> | undefined;
    let input: ReturnType<typeof bindTextInput> | undefined;
    let frame = 0;
    const observeSize = () => {
      const canvas = host.current?.querySelector("canvas");
      if (canvas?.width && canvas.height)
        setSize((current) =>
          current.width === canvas.width && current.height === canvas.height
            ? current
            : { width: canvas.width, height: canvas.height },
        );
    };
    const observer = new MutationObserver(observeSize);
    if (host.current)
      observer.observe(host.current, {
        subtree: true,
        childList: true,
        attributes: true,
        attributeFilter: ["width", "height"],
      });
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
              input = bindTextInput({
                root: host.current!,
                active,
                connection,
                commit: (text) => bridge?.pasteText(text),
              });
              textInput.current = input;
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
            setStatus("Disconnected");
            bridge?.stop();
            keyboard?.stop();
            input?.stop();
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
            setStatus("Connection rejected");
            bridge?.stop();
            keyboard?.stop();
            input?.stop();
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
      observer.disconnect();
      cancelAnimationFrame(frame);
      inputAllowed.current = false;
      bridge?.stop();
      keyboard?.stop();
      input?.stop();
      if (clipboard.current === bridge) clipboard.current = null;
      if (textInput.current === input) textInput.current = null;
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
      <div
        className={`${styles.desktop} ${scale === "fit" ? styles.desktopFit : ""}`}
        aria-label="Remote browser desktop"
        aria-busy={!connected || busy}
      >
        <div
          className={styles.desktopSurface}
          style={
            scale === "fit"
              ? undefined
              : { width: size.width * zoom, height: size.height * zoom }
          }
        >
          <div
            ref={host}
            data-browser-screen
            className={styles.screen}
            style={
              scale === "fit"
                ? undefined
                : { width: size.width * zoom, height: size.height * zoom }
            }
            inert={!interactive}
          />
        </div>
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
            onPointerDown={showHint}
            onClick={showHint}
            onContextMenu={(event) => {
              event.preventDefault();
              showHint();
            }}
          />
        )}
        {showControlHint && !interactive && (
          <div className={styles.controlOverlay}>
            <div className={styles.controlHint} role="status">
              <HandArrowDown size={28} aria-hidden="true" />
              <strong>
                {busy ? "Updating browser…" : "Take control to interact"}
              </strong>
              <span>
                {busy ? (
                  "Please wait."
                ) : controlToken ? (
                  "Wait for the connection or reconnect."
                ) : session.control === "human" ? (
                  <>
                    Click <HandArrowUp aria-label="Hand back to Agent" /> to
                    hand back, then <HandArrowDown aria-label="Take control" />{" "}
                    to take control here.
                  </>
                ) : (
                  <>
                    Click <HandArrowDown aria-label="Take control" /> in the
                    toolbar.
                  </>
                )}
              </span>
            </div>
          </div>
        )}
        {!!status && (
          <div className={styles.streamStatus}>
            <div className={styles.controlHint}>
              <strong role="status">{status}</strong>
              {canReconnect && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setReconnect((value) => value + 1)}
                >
                  <ArrowClockwise />
                  Reconnect
                </Button>
              )}
            </div>
          </div>
        )}
        {!!clipboardError && (
          <p className={styles.inputNotice} role="status">
            {clipboardError}
          </p>
        )}
      </div>
    </>
  );
}

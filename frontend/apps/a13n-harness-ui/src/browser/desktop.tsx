import {
  lazy,
  Suspense,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Button, ModalFrame } from "a13n-ui";
import { ArrowClockwise, Camera, HandArrowDown } from "@phosphor-icons/react";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { bindClipboard, waitForClipboardWrites } from "./clipboard";
import { bindKeyboard } from "./keyboard";
import { bindTextInput } from "./text-input";
import { connectWindowPreview } from "./window-preview";
import { MacCaptureSettings } from "./macos-setup";
import styles from "./panel.module.css";
import { ReturnToChat } from "../native/capture";

const AnnotationEditor = lazy(() =>
  import("./annotation-editor").catch(() => ({
    default: ({ onClose }: { onClose: () => void }) => (
      <CaptureStatus onClose={onClose} failed />
    ),
  })),
);

function CaptureStatus({
  onClose,
  failed = false,
}: {
  onClose: () => void;
  failed?: boolean;
}) {
  return (
    <ModalFrame
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      finalFocus={false}
      title={
        failed ? "Screenshot editor unavailable" : "Loading screenshot editor…"
      }
      description={
        failed
          ? "Reload the page and capture again."
          : "Preparing local editing tools."
      }
      closeLabel="Close"
      footer={
        <Button variant="outline" onClick={onClose}>
          Cancel
        </Button>
      }
    />
  );
}

export type BrowserScale = "fit" | "50" | "75" | "100" | "125" | "150" | "200";

export function Desktop({
  threadId,
  session,
  controlToken,
  busy,
  activity = "",
  onReconnectControl,
  scale = "fit",
  actions,
}: {
  threadId: string;
  session: Schema<"BrowserState">;
  controlToken: string;
  busy: boolean;
  activity?: string;
  onReconnectControl?: () => void;
  scale?: BrowserScale;
  actions?: ReactNode;
}) {
  const { client } = useTransport();
  const returnToChat = useContext(ReturnToChat);
  const captureButton = useRef<HTMLButtonElement>(null);
  const editing = useRef(false);
  const [capture, setCapture] = useState<{
    source: HTMLCanvasElement;
    ready: Promise<void>;
  } | null>(null);
  const closeCapture = (copied = false) => {
    editing.current = false;
    setCapture(null);
    clipboard.current?.resume();
    // Never focus the remote desktop after writing an image to the local clipboard.
    requestAnimationFrame(() => {
      if (copied) returnToChat();
      else captureButton.current?.focus();
    });
  };
  const host = useRef<HTMLDivElement>(null);
  const snapshot = useRef<HTMLCanvasElement>(null);
  const rfb = useRef<import("@novnc/novnc").default | null>(null);
  const [connectionState, setConnectionState] = useState({
    key: "",
    status: "Connecting…",
    retry: false,
  });
  const [clipboardError, setClipboardError] = useState("");
  const clipboard = useRef<ReturnType<typeof bindClipboard> | null>(null);
  const textInput = useRef<ReturnType<typeof bindTextInput> | null>(null);
  const inputAllowed = useRef(false);
  const [reconnect, setReconnect] = useState(0);
  const [canControl, setCanControl] = useState(false);
  const [readyConnection, setReadyConnection] = useState("");
  const connectionKey = JSON.stringify([
    threadId,
    session.session_id,
    session.control,
    controlToken,
    reconnect,
  ]);
  const connected = readyConnection === connectionKey;
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
  const nativePreview = session.platform === "macos";
  const zoom = scale === "fit" ? 1 : Number(scale) / 100;
  const interactive =
    connected &&
    canControl &&
    !!controlToken &&
    session.control === "human" &&
    !busy &&
    !capture;
  const initialStatus =
    controlToken && !nativePreview ? "Connecting control…" : "Connecting…";
  const currentConnection = connectionState.key === connectionKey;
  const canReconnect = currentConnection && connectionState.retry;
  const message =
    activity || (currentConnection ? connectionState.status : initialStatus);
  inputAllowed.current = interactive && !!controlToken;
  useEffect(() => {
    if (connected && canControl && controlToken && !busy && !editing.current) {
      textInput.current?.focus();
      clipboard.current?.sync();
    }
  }, [connected, canControl, controlToken, busy]);
  useEffect(() => {
    if (rfb.current) rfb.current.viewOnly = !interactive;
  }, [interactive]);
  useEffect(() => {
    let disposed = false;
    let failed = false;
    const admission = new AbortController();
    let connection: import("@novnc/novnc").default | undefined;
    let stopPreview: (() => void) | undefined;
    let previewCanvas: HTMLCanvasElement | undefined;
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
    const fail = (message: string) => {
      if (disposed || failed) return;
      failed = true;
      clearTimeout(timeout);
      admission.abort();
      retain();
      cancelAnimationFrame(frame);
      inputAllowed.current = false;
      bridge?.stop();
      keyboard?.stop();
      input?.stop();
      connection?.disconnect();
      stopPreview?.();
      setConnectionState({ key: connectionKey, status: message, retry: true });
      setReadyConnection("");
      setCanControl(false);
    };
    const timeout = setTimeout(
      () => fail("Connection timed out. Reconnect to try again."),
      15000,
    );
    setConnectionState({
      key: connectionKey,
      status: initialStatus,
      retry: false,
    });
    setReadyConnection("");
    setCanControl(false);
    setShowControlHint(false);
    setClipboardError("");
    void (async () => {
      try {
        const access = await result(
          client.POST("/api/threads/{thread_id}/browser/access", {
            params: { path: { thread_id: threadId } },
            signal: admission.signal,
            body: {
              session_id: session.session_id!,
              control_token: controlToken,
            },
          }),
        );
        if (disposed || failed) return;
        const url = new URL(
          `/api/threads/${encodeURIComponent(threadId)}/browser/connect`,
          window.location.origin,
        );
        url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
        url.searchParams.set("ticket", access.ticket);
        if (access.protocol === "jpeg") {
          previewCanvas = document.createElement("canvas");
          previewCanvas.className = styles.windowPreview;
          host.current!.append(previewCanvas);
          stopPreview = connectWindowPreview(
            url.toString(),
            previewCanvas,
            () => {
              if (disposed || failed) return;
              clearTimeout(timeout);
              setConnectionState({
                key: connectionKey,
                status: "",
                retry: false,
              });
              setReadyConnection(connectionKey);
            },
            () =>
              fail(
                "Window preview unavailable. Unlock the Mac, keep the browser window visible, and check Screen Recording permission, then reconnect. If the window closed, reset the browser. Automation can continue without preview.",
              ),
          );
          return;
        }
        const { default: RFB } = await import("@novnc/novnc");
        if (disposed || failed) return;
        connection = new RFB(host.current!, url.toString(), {
          credentials: { password: access.password },
        });
        rfb.current = connection;
        connection.viewOnly = true;
        connection.scaleViewport = true;
        connection.addEventListener("connect", () => {
          if (!disposed && !failed) {
            setShowControlHint(false);
            setConnectionState({
              key: connectionKey,
              status: "Waiting for the first frame…",
              retry: false,
            });
            const reveal = () => {
              if (disposed || failed) return;
              const saved = snapshot.current;
              const canvas = host.current?.querySelector("canvas");
              // Keep the last frame until noVNC paints the replacement connection.
              if (
                !canvas?.width ||
                !canvas.height ||
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
              clearTimeout(timeout);
              setConnectionState({
                key: connectionKey,
                status: "",
                retry: false,
              });
              setReadyConnection(connectionKey);
              setCanControl(access.control);
            };
            reveal();
            if (access.control) {
              const active = () =>
                inputAllowed.current && rfb.current === connection;
              input = bindTextInput({
                root: host.current!,
                active,
                connection: connection!,
                commit: (text) => bridge?.pasteText(text),
              });
              textInput.current = input;
              keyboard = bindKeyboard({
                root: host.current!,
                active,
                connection: connection!,
                targetMac: session.platform === "macos",
              });
              bridge = bindClipboard({
                root: host.current!,
                active,
                connection: connection!,
                shortcut: keyboard.shortcut,
                notice: setClipboardError,
              });
              clipboard.current = bridge;
              if (editing.current) void bridge.suspend();
            }
          }
        });
        connection.addEventListener("disconnect", () => fail("Disconnected"));
        connection.addEventListener("securityfailure", () =>
          fail("Connection rejected"),
        );
      } catch (cause) {
        fail(cause instanceof Error ? cause.message : String(cause));
      }
    })();
    return () => {
      retain();
      disposed = true;
      clearTimeout(timeout);
      admission.abort();
      stopPreview?.();
      previewCanvas?.remove();
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
    session.platform,
    session.control,
    controlToken,
    reconnect,
    connectionKey,
    nativePreview,
    initialStatus,
  ]);
  const captureFrame = () => {
    try {
      const canvas = host.current?.querySelector("canvas");
      const pixels =
        nativePreview && canvas?.width && canvas.height
          ? canvas
              .getContext("2d")
              ?.getImageData(0, 0, canvas.width, canvas.height)
          : rfb.current?.getImageData();
      if (
        !connected ||
        !pixels?.width ||
        !pixels.height ||
        pixels.data[3] !== 255
      )
        throw new Error("Wait for a browser frame before capturing.");
      const source = document.createElement("canvas");
      source.width = pixels.width;
      source.height = pixels.height;
      const context = source.getContext("2d");
      if (!context)
        throw new Error("Screenshot capture is unavailable in this browser.");
      context.putImageData(pixels, 0, 0);
      editing.current = true;
      inputAllowed.current = false;
      if (rfb.current) rfb.current.viewOnly = true;
      const ready = clipboard.current?.suspend() ?? waitForClipboardWrites();
      setClipboardError("");
      setCapture({ source, ready });
    } catch (error) {
      setClipboardError(
        error instanceof Error
          ? error.message
          : "Could not capture the browser.",
      );
    }
  };
  return (
    <>
      <div className={styles.toolbar}>
        <span className={styles.connectionState} role="status">
          {message ||
            (interactive
              ? "You have control"
              : session.control === "human"
                ? "Automation paused"
                : "Watching")}
        </span>
        <Button
          ref={captureButton}
          size="icon"
          variant="ghost"
          aria-label="Capture and annotate screenshot"
          disabled={!connected || busy || !!capture}
          onClick={captureFrame}
        >
          <Camera />
        </Button>
        {actions}
      </div>
      {capture && (
        <Suspense fallback={<CaptureStatus onClose={() => closeCapture()} />}>
          <AnnotationEditor
            source={capture.source}
            clipboardReady={capture.ready}
            onClose={() => closeCapture()}
            onCopied={() => closeCapture(true)}
          />
        </Suspense>
      )}
      <div
        className={`${styles.desktop} ${scale === "fit" ? styles.desktopFit : ""}`}
        aria-label={
          nativePreview
            ? "Read-only browser window preview"
            : "Remote browser desktop"
        }
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
        {!nativePreview && !interactive && (
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
        {!nativePreview && showControlHint && !interactive && !message && (
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
                    Click <HandArrowDown aria-label="Take control here" /> to
                    move control to this browser. Agent tools stay paused.
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
        {!!message && (
          <div className={styles.streamStatus}>
            <div className={styles.controlHint}>
              <strong>{message}</strong>
              {activity === "Taking control…" && (
                <span>Waiting for current browser operations to finish.</span>
              )}
              {!activity && controlToken && !nativePreview && (
                <span>
                  Automation stays paused. You can hand back at any time.
                </span>
              )}
              {nativePreview && canReconnect && <MacCaptureSettings />}
              {canReconnect && !busy && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => {
                    if (controlToken && !nativePreview && onReconnectControl)
                      onReconnectControl();
                    else setReconnect((value) => value + 1);
                  }}
                >
                  <ArrowClockwise />
                  {controlToken && !nativePreview
                    ? "Reconnect control"
                    : "Reconnect"}
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

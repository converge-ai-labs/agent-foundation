import { useEffect, useRef, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { Button } from "a13n-ui";
import { useTransport } from "../transport/context";
import { TerminalConnection, type TerminalState } from "./terminal-connection";
import "@xterm/xterm/css/xterm.css";
import styles from "./terminal.module.css";

export default function TerminalScreen({
  id,
  visible,
  unauthorized,
}: {
  id: string;
  visible: boolean;
  unauthorized: () => void;
}) {
  const transport = useTransport();
  const element = useRef<HTMLDivElement>(null);
  const connection = useRef<TerminalConnection | null>(null);
  const terminal = useRef<Terminal | null>(null);
  const [state, setState] = useState<TerminalState>({
    connection: "Connecting",
    frame: null,
    pendingControl: false,
    message: "",
  });
  useEffect(() => {
    const host = element.current!;
    const term = new Terminal({
      cursorBlink: true,
      fontSize: 13,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
      scrollback: 2000,
      disableStdin: true,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(host);
    terminal.current = term;
    const paintTheme = () => {
      const dark = document.documentElement.classList.contains("dark");
      term.options.theme = dark
        ? {
            background: "#141416",
            foreground: "#e4e4e7",
            cursor: "#e4e4e7",
            selectionBackground: "#3f3f4680",
          }
        : {
            background: "#ffffff",
            foreground: "#27272a",
            cursor: "#27272a",
            selectionBackground: "#a1a1aa60",
          };
    };
    paintTheme();
    const themeObserver = new MutationObserver(paintTheme);
    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
    let animation = 0;
    const resize = () => {
      cancelAnimationFrame(animation);
      animation = requestAnimationFrame(() => {
        if (!host.clientWidth || !host.clientHeight) return;
        if (session.controls) {
          const dimensions = fit.proposeDimensions();
          if (dimensions) {
            const cols = Math.min(1000, Math.max(2, dimensions.cols));
            const rows = Math.min(1000, Math.max(1, dimensions.rows));
            term.resize(cols, rows);
            session.resize(rows, cols);
          }
        } else if (session.state.frame) {
          const view = session.state.frame.terminal;
          term.resize(view.columns ?? 80, view.rows ?? 24);
        }
      });
    };
    let lastLayout = "";
    const session = new TerminalConnection(
      id,
      {
        write: (text, done) => term.write(text, done),
        reset: () => term.reset(),
      },
      (next) => {
        setState(next);
        term.options.disableStdin = !session.controls;
        // A confirmed control/size change, not output volume, drives fitting.
        const layout = `${session.controls}:${next.frame?.terminal.rows}:${next.frame?.terminal.columns}`;
        if (layout !== lastLayout) {
          lastLayout = layout;
          resize();
        }
      },
      unauthorized,
    );
    connection.current = session;
    const data = term.onData((text) => session.input(text));
    const binary = term.onBinary(() =>
      setState((value) => ({
        ...value,
        message:
          "This terminal protocol accepts UTF-8 input. Legacy binary mouse reports are not sent.",
      })),
    );
    const observer = new ResizeObserver(resize);
    observer.observe(host);
    session.connect(window.location.origin, transport.key);
    return () => {
      session.dispose();
      connection.current = null;
      terminal.current = null;
      observer.disconnect();
      themeObserver.disconnect();
      cancelAnimationFrame(animation);
      data.dispose();
      binary.dispose();
      term.dispose();
    };
  }, [id, transport, unauthorized]);
  useEffect(() => {
    if (!visible) connection.current?.detach();
  }, [visible]);
  const session = connection.current;
  const view = state.frame?.terminal;
  return (
    <div className={styles.screen}>
      <div className={styles.controlBar}>
        <span role="status">
          {state.connection} ·{" "}
          {session?.controls ? "You control input" : "Read-only viewer"}
        </span>
        {view && (
          <small>
            {view.participants.length} attached · epoch {view.control_epoch} ·{" "}
            {view.columns} × {view.rows} · {view.state}
            {view.exit_code !== null ? ` (${view.exit_code})` : ""}
          </small>
        )}
        {state.connection === "Detached" ? (
          <Button
            size="sm"
            variant="outline"
            disabled={session?.draining}
            onClick={() =>
              session?.connect(window.location.origin, transport.key)
            }
          >
            Reattach
          </Button>
        ) : (
          <Button size="sm" variant="ghost" onClick={() => session?.detach()}>
            Detach
          </Button>
        )}
        <Button
          size="sm"
          variant="outline"
          disabled={
            state.connection !== "Live" ||
            state.pendingControl ||
            view?.state !== "running"
          }
          onClick={() => {
            session?.control(!!session.controls);
            terminal.current?.focus();
          }}
        >
          {state.pendingControl
            ? "Confirming…"
            : session?.controls
              ? "Release control"
              : view?.controller
                ? "Take over input"
                : "Take control"}
        </Button>
      </div>
      {view?.controller && !session?.controls && (
        <small>
          Controller: {view.controller}. Takeover affects the shared session,
          not other viewers.
        </small>
      )}
      {state.message && (
        <p className={styles.message} role="status">
          {state.message}
        </p>
      )}
      <div className={styles.viewport}>
        <div
          ref={element}
          className={styles.emulator}
          aria-label="Native terminal output and input"
        />
      </div>
      <small>
        Server / container · native OS user, independent of the Agent
        Environment. Read-only size follows the controller; scroll horizontally
        when needed. Retention: 1 MiB server bytes, 2,000 local scrollback
        lines. Reattach never replays input.
      </small>
    </div>
  );
}

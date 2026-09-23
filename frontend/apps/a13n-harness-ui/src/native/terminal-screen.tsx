import { useContext, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { SearchAddon } from "@xterm/addon-search";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { ComposerDrafts } from "../conversations/composer";
import type { Schema } from "../transport/client";
import { terminalFileLinks } from "./terminal-links";
import { TextField } from "../shell/ui";
import { ReturnToChat } from "./capture";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import {
  MagnifyingGlass,
  Copy,
  ClipboardText,
  ChatText,
  Plug,
  Plugs,
  Hand,
  HandPalm,
  CircleNotch,
  ArrowUp,
  ArrowDown,
  X,
  ArrowBendUpLeft,
} from "@phosphor-icons/react";
import { TerminalAction } from "./terminal-action";
import { terminalShortcut, terminalShortcutLabels } from "./terminal-shortcuts";
import { useTransport } from "../transport/context";
import { TerminalConnection, type TerminalState } from "./terminal-connection";
import "@xterm/xterm/css/xterm.css";
import styles from "./terminal.module.css";

const searchOptions = {
  decorations: {
    matchOverviewRuler: "#a16207",
    activeMatchColorOverviewRuler: "#2563eb",
    matchBackground: "#854d0e",
    activeMatchBackground: "#1d4ed8",
  },
};

export default function TerminalScreen({
  id,
  visible,
  claimCreated,
  unauthorized,
  threadId,
  openFile,
}: {
  id: string;
  visible: boolean;
  claimCreated?: () => boolean;
  unauthorized: () => void;
  threadId?: string;
  openFile?: (path: string, line?: number) => void;
}) {
  const transport = useTransport();
  const shortcuts = terminalShortcutLabels();
  const shown = useRef(visible);
  shown.current = visible;
  const queries = useQueryClient();
  const drafts = useContext(ComposerDrafts);
  const returnToChat = useContext(ReturnToChat);
  const searchElement = useRef<HTMLDivElement>(null);
  const opener = useRef(openFile);
  opener.current = openFile;
  const resumeOnShow = useRef(false);
  const search = useRef<SearchAddon | null>(null);
  const [finding, setFinding] = useState(false);
  const [query, setQuery] = useState("");
  const [matches, setMatches] = useState("");
  const [selection, setSelection] = useState("");
  const [feedback, setFeedback] = useState("");
  const claim = useRef(claimCreated);
  claim.current = claimCreated;
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
      // Search decorations use xterm's proposed decoration API.
      allowProposedApi: true,
      cursorBlink: true,
      fontSize: 13,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
      scrollback: 2000,
      disableStdin: true,
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    const finder = new SearchAddon();
    term.loadAddon(finder);
    search.current = finder;
    term.loadAddon(
      new WebLinksAddon((_event, uri) => {
        if (/^https?:\/\//i.test(uri))
          window.open(uri, "_blank", "noopener,noreferrer");
      }),
    );
    term.open(host);
    const linkProvider = term.registerLinkProvider({
      provideLinks: (y, callback) => {
        const row = term.buffer.active.getLine(y - 1);
        if (!row || !opener.current) {
          callback(undefined);
          return;
        }
        const text = row.translateToString(true);
        // Wide characters and wrapped lines require cell-aware mapping. Do not
        // mis-link an approximate location; leave those paths selectable instead.
        if (row.isWrapped || /[^\x20-\x7e]/.test(text)) {
          callback(undefined);
          return;
        }
        callback(
          terminalFileLinks(text).map((link) => ({
            range: {
              start: { x: link.start + 1, y },
              end: { x: link.start + link.text.length, y },
            },
            text: link.text,
            activate: () => opener.current?.(link.path, link.line),
          })),
        );
      },
    });
    const selected = term.onSelectionChange(() =>
      setSelection(term.getSelection()),
    );
    const searchResults = finder.onDidChangeResults(
      ({ resultIndex, resultCount }) =>
        setMatches(
          resultIndex < 0 && resultCount
            ? `${resultCount}+ matches`
            : `${resultIndex + 1} / ${resultCount}`,
        ),
    );

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
    let controlled = false;
    let lastState = "";
    const session = new TerminalConnection(
      id,
      {
        write: (text, done) => term.write(text, done),
        reset: () => term.reset(),
      },
      (next) => {
        setState(next);
        if (next.frame && next.frame.terminal.state !== lastState) {
          lastState = next.frame.terminal.state;
          const view = next.frame.terminal;
          queries.setQueryData<Schema<"TerminalView">[]>(
            ["native", "terminals"],
            (items) =>
              items?.map((item) => (item.terminal_id === id ? view : item)),
          );
        }
        term.options.disableStdin = !session.controls;
        if (shown.current && session.controls && !controlled) term.focus();
        controlled = session.controls;
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
    if (visible)
      session.connect(
        window.location.origin,
        transport.key,
        () => claim.current?.() ?? false,
      );
    else resumeOnShow.current = true;
    return () => {
      session.dispose();
      connection.current = null;
      terminal.current = null;
      observer.disconnect();
      themeObserver.disconnect();
      cancelAnimationFrame(animation);
      data.dispose();
      binary.dispose();
      selected.dispose();
      searchResults.dispose();
      linkProvider.dispose();
      search.current = null;
      term.dispose();
    };
  }, [id, transport, unauthorized]);
  useEffect(() => {
    const session = connection.current;
    if (!session) return;
    if (!visible && session.state.connection !== "Detached") {
      resumeOnShow.current = true;
      session.detach();
    } else if (visible && resumeOnShow.current && !session.draining) {
      resumeOnShow.current = false;
      session.connect(window.location.origin, transport.key);
    }
  }, [visible, state, transport]);
  useEffect(() => {
    if (visible) terminal.current?.focus();
  }, [visible]);
  useEffect(() => {
    setFeedback("");
  }, [threadId]);
  useEffect(() => {
    if (finding) searchElement.current?.querySelector("input")?.focus();
  }, [finding]);
  useEffect(() => {
    if (!finding) {
      search.current?.clearDecorations();
      setMatches("");
      return;
    }
    if (!query) {
      search.current?.clearDecorations();
      setMatches("");
      return;
    }
    search.current?.findNext(query, {
      incremental: true,
      ...searchOptions,
    });
  }, [finding, query]);
  const addSelection = () => {
    const draft = threadId ? drafts.get(threadId) : undefined;
    if (!draft?.synchronized || draft.replacement) {
      setFeedback("Reconnect the conversation's shared input first.");
      return;
    }
    const text = draft.doc.getText("text");
    const fence = "`".repeat(
      Math.max(
        3,
        ...[...selection.matchAll(/`+/g)].map((match) => match[0].length + 1),
      ),
    );
    const excerpt = `\n\nSelected output from Server terminal ${id} (not a complete log):\n${fence}text\n${selection}\n${fence}\n`;
    if (text.length + excerpt.length > 256 * 1024 || excerpt.includes("\0")) {
      setFeedback(
        "Selection exceeds the shared input limit or contains NUL. Select less output; nothing was added.",
      );
      return;
    }
    draft.undo.stopCapturing();
    text.insert(text.length, excerpt);
    draft.undo.stopCapturing();
    setFeedback("Added to this conversation's input. Review before sending.");
  };
  const copySelection = async () => {
    const text = terminal.current?.getSelection();
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setFeedback("Selection copied.");
    } catch {
      setFeedback("Copy unavailable. Use your browser's copy action.");
    }
  };
  const pasteClipboard = async () => {
    const session = connection.current;
    const term = terminal.current;
    if (!session?.controls || !term) return;
    const epoch = session.state.frame?.terminal.control_epoch;
    try {
      const text = await navigator.clipboard.readText();
      // Clipboard permission can resolve after a detach or control transfer.
      if (
        !shown.current ||
        connection.current !== session ||
        !session.controls ||
        session.state.frame?.terminal.control_epoch !== epoch
      )
        return;
      term.paste(text);
      term.focus();
    } catch {
      setFeedback("Paste unavailable. Use your browser's paste action.");
    }
  };
  const openSearch = () => {
    setFinding(true);
    searchElement.current?.querySelector("input")?.focus();
  };
  const closeSearch = () => {
    setFinding(false);
    terminal.current?.focus();
  };
  const session = connection.current;
  const view = state.frame?.terminal;
  const controlLabel = state.pendingControl
    ? "Confirming…"
    : session?.controls
      ? "Release control"
      : view?.controller
        ? "Take over input"
        : "Take control";
  return (
    <div
      className={styles.screen}
      onKeyDownCapture={(event) => {
        if (!visible || event.defaultPrevented) return;
        const action = terminalShortcut(event.nativeEvent);
        if (action === "find") {
          event.preventDefault();
          event.stopPropagation();
          openSearch();
        } else if (
          element.current?.contains(event.target as Node) &&
          (action === "copy" || action === "paste")
        ) {
          event.preventDefault();
          event.stopPropagation();
          if (!event.repeat)
            void (action === "copy" ? copySelection() : pasteClipboard());
        }
      }}
    >
      <div className={styles.controlBar}>
        <div className={styles.connectionStatus}>
          <span role="status">
            {state.connection === "Connecting"
              ? "Connecting…"
              : state.connection === "Detached"
                ? "Disconnected"
                : state.pendingControl
                  ? "Confirming control…"
                  : session?.controls
                    ? "You have control"
                    : "Viewing only"}
          </span>
          {view && (
            <small
              title={`Session ${id} · ${view.columns} × ${view.rows} · control epoch ${view.control_epoch}`}
            >
              {view.participants.length} connected
              {view.state === "exited"
                ? ` · Exited (${view.exit_code ?? "unknown"})`
                : ""}
            </small>
          )}
        </div>
        <div className={styles.actions}>
          <TerminalAction
            label="Find in output"
            shortcut={shortcuts.find}
            aria-pressed={finding}
            onClick={openSearch}
          >
            <MagnifyingGlass />
          </TerminalAction>
          <TerminalAction
            label="Copy selection"
            shortcut={shortcuts.copy}
            disabled={!selection}
            onClick={() => void copySelection()}
          >
            <Copy />
          </TerminalAction>
          <TerminalAction
            label="Paste"
            shortcut={shortcuts.paste}
            disabled={!session?.controls}
            onClick={() => void pasteClipboard()}
          >
            <ClipboardText />
          </TerminalAction>
          <TerminalAction
            label="Add selection to message"
            disabled={!selection || !threadId}
            onClick={addSelection}
          >
            <ChatText />
          </TerminalAction>
          {state.connection === "Detached" ? (
            <TerminalAction
              label="Reconnect"
              disabled={session?.draining}
              onClick={() =>
                session?.connect(window.location.origin, transport.key)
              }
            >
              <Plug />
            </TerminalAction>
          ) : (
            <TerminalAction
              label="Disconnect"
              onClick={() => {
                resumeOnShow.current = false;
                session?.detach();
              }}
            >
              <Plugs />
            </TerminalAction>
          )}
          <TerminalAction
            label={controlLabel}
            aria-pressed={!!session?.controls}
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
            {state.pendingControl ? (
              <CircleNotch />
            ) : session?.controls ? (
              <HandPalm />
            ) : (
              <Hand />
            )}
          </TerminalAction>
        </div>
      </div>
      {finding && (
        <div
          ref={searchElement}
          className={styles.search}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.preventDefault();
              event.stopPropagation();
              closeSearch();
            }
            if (event.key === "Enter") {
              event.preventDefault();
              event.shiftKey
                ? search.current?.findPrevious(query, searchOptions)
                : search.current?.findNext(query, searchOptions);
            }
          }}
        >
          <TextField
            label="Find in retained terminal output"
            value={query}
            onChange={setQuery}
          />
          <small role="status">{matches}</small>
          <TerminalAction
            label="Previous match"
            shortcut="Shift+Enter"
            disabled={!query}
            onClick={() => search.current?.findPrevious(query, searchOptions)}
          >
            <ArrowUp />
          </TerminalAction>
          <TerminalAction
            label="Next match"
            shortcut="Enter"
            disabled={!query}
            onClick={() => search.current?.findNext(query, searchOptions)}
          >
            <ArrowDown />
          </TerminalAction>
          <TerminalAction
            label="Close search"
            shortcut="Escape"
            onClick={closeSearch}
          >
            <X />
          </TerminalAction>
        </div>
      )}
      {feedback && (
        <div className={styles.actions}>
          <p className={styles.message} role="status">
            {feedback}
          </p>
          {feedback.startsWith("Added") && (
            <TerminalAction
              label="Return to conversation"
              onClick={returnToChat}
            >
              <ArrowBendUpLeft />
            </TerminalAction>
          )}
        </div>
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
      <small title="Runs as the server's OS user, not in the agent's environment. Viewers share the controller's terminal size. Reconnecting does not replay input.">
        Server terminal
        {view?.controller && !session?.controls
          ? " · Someone else has control"
          : ""}
      </small>
    </div>
  );
}

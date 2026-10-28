import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { copyText, modifierLabel, usesCommandKey } from "../platform";
import { readPreference, writePreference } from "./preferences";
import styles from "./debug.module.css";

/**
 * Global Debug mode.
 *
 * Off by default. When enabled, surfaces reveal data-level details such as
 * identifiers, raw payloads, events and token counts. Toggled from Settings or
 * with Mod+Alt+D, persisted per browser, and reflected on the document root as
 * `data-debug` so styles can react without prop drilling.
 */
type DebugState = { enabled: boolean; setEnabled: (enabled: boolean) => void };

const DebugContext = createContext<DebugState>({
  enabled: false,
  setEnabled: () => undefined,
});

export const debugShortcutLabel = () =>
  usesCommandKey() ? `${modifierLabel()}⌥D` : `${modifierLabel()}+Alt+D`;

export function isDebugShortcut(event: KeyboardEvent): boolean {
  const mod = usesCommandKey() ? event.metaKey : event.ctrlKey;
  return mod && event.altKey && !event.shiftKey && event.code === "KeyD";
}

export function DebugProvider({ children }: { children: ReactNode }) {
  const [enabled, setEnabledState] = useState(
    () => readPreference("debug", "off") === "on",
  );
  const setEnabled = useCallback((next: boolean) => {
    setEnabledState(next);
  }, []);
  useEffect(() => {
    writePreference("debug", enabled ? "on" : "off");
    if (enabled) document.documentElement.dataset.debug = "";
    else delete document.documentElement.dataset.debug;
  }, [enabled]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!isDebugShortcut(event)) return;
      event.preventDefault();
      setEnabledState((value) => !value);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const value = useMemo(() => ({ enabled, setEnabled }), [enabled, setEnabled]);
  return (
    <DebugContext.Provider value={value}>{children}</DebugContext.Provider>
  );
}

export function useDebug() {
  return useContext(DebugContext);
}

/** Render children only while Debug mode is enabled. */
export function DebugOnly({ children }: { children: ReactNode }) {
  return useDebug().enabled ? <>{children}</> : null;
}

/** Clipboard denial is recoverable and must never become an unhandled error. */
function useDebugCopy() {
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const timer = setTimeout(() => setCopied(false), 1200);
    return () => clearTimeout(timer);
  }, [copied]);
  const copy = async (text: string) => {
    setCopied(false);
    setFailed(false);
    try {
      await copyText(text);
      setCopied(true);
    } catch {
      setFailed(true);
    }
  };
  return { copied, failed, copy };
}

/** Inline identifier chip shown in Debug mode; click copies the value. */
export function DebugId({
  label,
  value,
}: {
  label?: string;
  value?: string | null;
}) {
  const { enabled } = useDebug();
  const { copied, failed, copy } = useDebugCopy();
  if (!enabled || !value) return null;
  return (
    <button
      type="button"
      className={styles.id}
      title={
        failed ? "Could not copy. Try again." : `Copy ${label ?? "identifier"}`
      }
      onClick={(event) => {
        event.stopPropagation();
        void copy(value);
      }}
    >
      {label && <span className={styles.idLabel}>{label}</span>}
      <span>{copied ? "Copied" : value}</span>
      {failed && <span role="alert">Copy failed</span>}
    </button>
  );
}

/** Collapsible raw JSON view shown in Debug mode. */
export function DebugData({
  value,
  label = "Raw data",
  open,
}: {
  value: unknown;
  label?: string;
  open?: boolean;
}) {
  const { enabled } = useDebug();
  const { copied, failed, copy } = useDebugCopy();
  if (!enabled) return null;
  const text = JSON.stringify(value, null, 2) ?? "undefined";
  return (
    <div className={styles.data}>
      <details open={open}>
        <summary>
          <span className={styles.badge}>Debug</span>
          <span>{label}</span>
        </summary>
        <pre tabIndex={0} role="region" aria-label={label}>
          {text}
        </pre>
      </details>
      <button
        type="button"
        className={styles.copy}
        onClick={() => void copy(text)}
      >
        {copied ? "Copied" : "Copy"}
      </button>
      {failed && (
        <p role="alert" className={styles.copyFailure}>
          Copy failed. Try again or select the text.
        </p>
      )}
    </div>
  );
}

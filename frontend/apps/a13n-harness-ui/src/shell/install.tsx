import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Button } from "a13n-ui";
import { Panel } from "./ui";

type InstallPrompt = Event & {
  prompt: () => Promise<{ outcome: "accepted" | "dismissed" }>;
};
type Installation = {
  available: boolean;
  standalone: boolean;
  installed: boolean;
  pending: boolean;
  error: string;
  request: () => Promise<void>;
};
const InstallContext = createContext<Installation | null>(null);

// Mounted at startup: the browser may offer installation before Settings opens.
export function InstallProvider({ children }: { children: ReactNode }) {
  const prompt = useRef<InstallPrompt | null>(null);
  const [available, setAvailable] = useState(false);
  const [standalone, setStandalone] = useState(false);
  const [installed, setInstalled] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    const display = window.matchMedia("(display-mode: standalone)");
    const refresh = () =>
      setStandalone(
        display.matches ||
          ("standalone" in navigator && navigator.standalone === true),
      );
    const offer = (event: Event) => {
      if (!("prompt" in event) || typeof event.prompt !== "function") return;
      event.preventDefault();
      prompt.current = event as InstallPrompt;
      setAvailable(true);
      setError("");
    };
    const installed = () => {
      prompt.current = null;
      setAvailable(false);
      setInstalled(true);
      setError("");
    };
    refresh();
    display.addEventListener("change", refresh);
    window.addEventListener("beforeinstallprompt", offer);
    window.addEventListener("appinstalled", installed);
    return () => {
      display.removeEventListener("change", refresh);
      window.removeEventListener("beforeinstallprompt", offer);
      window.removeEventListener("appinstalled", installed);
    };
  }, []);
  const request = async () => {
    const offered = prompt.current;
    if (!offered) return;
    // A prompt is single-use; dismissal or failure never triggers a retry.
    prompt.current = null;
    setAvailable(false);
    setPending(true);
    setError("");
    try {
      await offered.prompt();
      // Accepting the dialog is not proof of a completed installation.
    } catch {
      setError(
        "Installation could not be opened. Try your browser's app menu.",
      );
    } finally {
      setPending(false);
    }
  };
  return (
    <InstallContext
      value={{ available, standalone, installed, pending, error, request }}
    >
      {children}
    </InstallContext>
  );
}

export function InstallSettings() {
  const install = useContext(InstallContext);
  return (
    <Panel title="Install Harness UI">
      <p>
        Open this workspace from your desktop or home screen in its own window.
        The Harness UI server must still be running and reachable.
      </p>
      {install?.standalone ? (
        <p>Running in an app window.</p>
      ) : install?.installed ? (
        <p role="status">Installed. Open Harness UI from your apps.</p>
      ) : !window.isSecureContext ? (
        <p>Installation requires HTTPS or a localhost address.</p>
      ) : (
        <>
          {(install?.available || install?.pending) && (
            <div>
              <Button
                variant="outline"
                loading={install.pending}
                onClick={() => void install.request()}
              >
                Install app
              </Button>
            </div>
          )}
          <details>
            <summary>Manual installation</summary>
            <p>
              Use your browser's Install app or Add to Home Screen option, when
              available. On iPhone or iPad, open this page in Safari and choose
              Share, then Add to Home Screen. On Mac, Safari offers File → Add
              to Dock.
            </p>
          </details>
        </>
      )}
      {install?.error && <p role="alert">{install.error}</p>}
    </Panel>
  );
}

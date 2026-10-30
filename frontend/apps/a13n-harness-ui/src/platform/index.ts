/**
 * Platform adapter.
 *
 * The WebUI runs in a browser served by the Harness UI listener today. A
 * future desktop shell (for example Electron) can load the same bundle from a
 * custom protocol and inject `window.a13nDesktop` from its preload script.
 * Every host-dependent capability goes through this module so feature code
 * never assumes a same-origin browser tab.
 */

export interface DesktopBridge {
  /** Absolute origin of the Harness UI listener, such as `http://127.0.0.1:8765`. */
  apiOrigin?: string;
  /** Operating system reported by the desktop shell. */
  platform?: "darwin" | "win32" | "linux";
  openExternal?: (url: string) => void | Promise<void>;
  copyText?: (text: string) => void | Promise<void>;
  readText?: () => string | Promise<string>;
}

declare global {
  interface Window {
    a13nDesktop?: DesktopBridge;
  }
}

function bridge(): DesktopBridge | undefined {
  return typeof window === "undefined" ? undefined : window.a13nDesktop;
}

/** Whether the bundle runs inside a desktop shell rather than a browser tab. */
export function isDesktop(): boolean {
  return bridge() !== undefined;
}

/** Desktop operating system when running inside a desktop shell. */
export function desktopPlatform(): DesktopBridge["platform"] {
  return bridge()?.platform;
}

/**
 * Origin used for API and WebSocket requests. Browsers use the page origin;
 * a desktop shell or a build-time override can point at a separate listener.
 */
export function apiOrigin(): string {
  const configured =
    bridge()?.apiOrigin ??
    (import.meta as { env?: Record<string, string | undefined> }).env
      ?.VITE_A13N_API_ORIGIN;
  if (configured) return new URL(configured).origin;
  return window.location.origin;
}

/** Resolve an API path against the configured listener origin. */
export function apiUrl(path: string): URL {
  return new URL(path, apiOrigin());
}

/** Open a URL outside the application window. */
export function openExternal(url: string): void {
  const desktop = bridge();
  if (desktop?.openExternal) {
    void desktop.openExternal(url);
    return;
  }
  window.open(url, "_blank", "noopener,noreferrer");
}

/** Copy text to the system clipboard. */
export async function copyText(text: string): Promise<void> {
  const desktop = bridge();
  if (desktop?.copyText) return desktop.copyText(text);
  await navigator.clipboard.writeText(text);
}

/** Read text from the system clipboard. */
export async function readText(): Promise<string> {
  const desktop = bridge();
  if (desktop?.readText) return desktop.readText();
  return navigator.clipboard.readText();
}

/** Whether the primary shortcut modifier is Command rather than Control. */
export function usesCommandKey(): boolean {
  const desktop = desktopPlatform();
  if (desktop) return desktop === "darwin";
  return /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
}

/** Display label for the primary shortcut modifier. */
export function modifierLabel(): string {
  return usesCommandKey() ? "⌘" : "Ctrl";
}

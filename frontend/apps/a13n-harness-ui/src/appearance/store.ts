import {
  defaultAppearance,
  parseAppearance,
  validSize,
  type Appearance,
  type SizeRegion,
  type Theme,
} from "./model";

export const APPEARANCE_KEY = "a13n-harness-ui.appearance";
export const LEGACY_THEME_KEY = "a13n-harness-ui.theme";
type Snapshot = {
  appearance: Appearance;
  dark: boolean;
  terminalDark: boolean;
  saveFailed: boolean;
};
const variables: Record<SizeRegion, string> = {
  navigation: "--harness-nav-font",
  interface: "--a13n-text-body",
  conversation: "--harness-prose-size",
  composer: "--composer-font-size",
  code: "--harness-code-size",
  terminal: "--harness-terminal-size",
};

/** One browser-local owner, independent of authentication and server state. */
export class AppearanceStore {
  private snapshot: Snapshot;
  private listeners = new Set<() => void>();
  private media: MediaQueryList | undefined;
  private started = false;
  constructor(private host: Window) {
    let appearance = defaultAppearance();
    let migrate = false;
    try {
      const raw = host.localStorage.getItem(APPEARANCE_KEY);
      appearance = parseAppearance(raw);
      if (raw === null) {
        const legacy = host.localStorage.getItem(LEGACY_THEME_KEY);
        if (legacy === "dark" || legacy === "light") {
          appearance.theme = legacy;
          migrate = true;
        }
      }
    } catch {
      /* Defaults remain usable when storage is unavailable. */
    }
    const dark = this.resolveDark(appearance.theme);
    this.snapshot = {
      appearance,
      dark,
      terminalDark: this.resolveTerminalDark(appearance, dark),
      saveFailed: false,
    };
    if (migrate) this.persist();
  }
  getSnapshot = () => this.snapshot;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private resolveDark(theme: Theme) {
    return (
      theme === "dark" ||
      (theme === "system" &&
        !!this.host.matchMedia?.("(prefers-color-scheme: dark)").matches)
    );
  }
  private resolveTerminalDark(appearance: Appearance, appDark: boolean) {
    return appearance.terminalTheme === "dark"
      ? true
      : appearance.terminalTheme === "light"
        ? false
        : appDark;
  }
  private paint() {
    const root = this.host.document.documentElement;
    root.classList.toggle("dark", this.snapshot.dark);
    for (const region of Object.keys(variables) as SizeRegion[]) {
      root.style.setProperty(
        variables[region],
        `${this.snapshot.appearance.sizes[region]}px`,
      );
    }
  }
  private notify() {
    this.paint();
    this.listeners.forEach((listener) => listener());
  }
  private systemChanged = () => {
    const dark = this.resolveDark(this.snapshot.appearance.theme);
    this.snapshot = {
      ...this.snapshot,
      dark,
      terminalDark: this.resolveTerminalDark(this.snapshot.appearance, dark),
    };
    this.notify();
  };
  private listenToSystem() {
    this.media?.removeEventListener("change", this.systemChanged);
    this.media = undefined;
    if (this.started && this.snapshot.appearance.theme === "system") {
      this.media = this.host.matchMedia?.("(prefers-color-scheme: dark)");
      this.media?.addEventListener("change", this.systemChanged);
    }
  }
  private storageChanged = (event: StorageEvent) => {
    if (event.key !== APPEARANCE_KEY && event.key !== null) return;
    if (event.storageArea && event.storageArea !== this.host.localStorage)
      return;
    const appearance = parseAppearance(event.newValue);
    const dark = this.resolveDark(appearance.theme);
    this.snapshot = {
      appearance,
      dark,
      terminalDark: this.resolveTerminalDark(appearance, dark),
      saveFailed: false,
    };
    this.listenToSystem();
    this.notify();
  };
  start = () => {
    if (!this.started) {
      this.started = true;
      this.host.addEventListener("storage", this.storageChanged);
      this.listenToSystem();
    }
    const dark = this.resolveDark(this.snapshot.appearance.theme);
    this.snapshot = {
      ...this.snapshot,
      dark,
      terminalDark: this.resolveTerminalDark(this.snapshot.appearance, dark),
    };
    this.paint();
    return this.stop;
  };
  private stop = () => {
    this.started = false;
    this.host.removeEventListener("storage", this.storageChanged);
    this.media?.removeEventListener("change", this.systemChanged);
    this.media = undefined;
  };
  private persist() {
    let saveFailed = false;
    try {
      this.host.localStorage.setItem(
        APPEARANCE_KEY,
        JSON.stringify(this.snapshot.appearance),
      );
    } catch {
      saveFailed = true;
    }
    if (!saveFailed) {
      try {
        this.host.localStorage.removeItem(LEGACY_THEME_KEY);
      } catch {
        /* The new snapshot already takes precedence. */
      }
    }
    this.snapshot = { ...this.snapshot, saveFailed };
  }
  private update(appearance: Appearance) {
    const dark = this.resolveDark(appearance.theme);
    this.snapshot = {
      ...this.snapshot,
      appearance,
      dark,
      terminalDark: this.resolveTerminalDark(appearance, dark),
    };
    this.listenToSystem();
    this.notify();
    this.persist();
    this.listeners.forEach((listener) => listener());
  }
  setTheme = (theme: Theme) =>
    this.update({ ...this.snapshot.appearance, theme });
  setTerminalTheme = (terminalTheme: Theme) =>
    this.update({ ...this.snapshot.appearance, terminalTheme });
  setSize = (region: SizeRegion, size: number) => {
    if (!validSize(region, size)) return;
    this.update({
      ...this.snapshot.appearance,
      sizes: { ...this.snapshot.appearance.sizes, [region]: size },
    });
  };
  reset = () => this.update(defaultAppearance());
}
let browserStore: AppearanceStore | undefined;
export function appearanceStore() {
  return (browserStore ??= new AppearanceStore(window));
}

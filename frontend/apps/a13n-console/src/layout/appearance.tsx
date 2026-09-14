import {
  createContext,
  useContext,
  useLayoutEffect,
  useState,
  type ReactNode,
} from "react";

type Theme = "system" | "light" | "dark";
const key = "a13n-console-theme";
const AppearanceContext = createContext<{
  theme: Theme;
  setTheme: (theme: Theme) => void;
} | null>(null);
export function AppearanceProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      const value = localStorage.getItem(key);
      return value === "light" || value === "dark" ? value : "system";
    } catch {
      return "system";
    }
  });
  useLayoutEffect(() => {
    const system = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      document.documentElement.classList.toggle(
        "dark",
        theme === "dark" || (theme === "system" && system.matches),
      );
    };
    apply();
    system.addEventListener("change", apply);
    try {
      localStorage.setItem(key, theme);
    } catch {
      /* Keep the preference for this visit when storage is unavailable. */
    }
    return () => system.removeEventListener("change", apply);
  }, [theme]);
  return (
    <AppearanceContext.Provider value={{ theme, setTheme }}>
      {children}
    </AppearanceContext.Provider>
  );
}
export function useAppearance() {
  const value = useContext(AppearanceContext);
  if (!value) throw new Error("AppearanceProvider is required.");
  return value;
}

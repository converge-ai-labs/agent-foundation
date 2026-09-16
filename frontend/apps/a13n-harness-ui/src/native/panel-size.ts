import { useEffect, useState } from "react";

/** Only layout preferences are durable; file content and terminal output are not. */
export function usePanelSize(
  key: string,
  initial: number,
  min: number,
  max: number,
) {
  const [size, setSize] = useState(() => {
    try {
      const stored = localStorage.getItem(key);
      const value = stored === null ? initial : Number(stored);
      return Number.isFinite(value)
        ? Math.max(min, Math.min(max, value))
        : initial;
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try {
      localStorage.setItem(key, String(size));
    } catch {
      /* Optional browser preference. */
    }
  }, [key, size]);
  return [size, setSize] as const;
}

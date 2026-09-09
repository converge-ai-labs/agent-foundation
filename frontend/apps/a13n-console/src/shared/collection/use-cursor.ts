import { useState } from "react";
export function useCursor() {
  const [history, setHistory] = useState<(string | undefined)[]>([undefined]);
  return {
    cursor: history[history.length - 1],
    next: (cursor: string) => setHistory((previous) => [...previous, cursor]),
    previous:
      history.length > 1
        ? () => setHistory((previous) => previous.slice(0, -1))
        : undefined,
    reset: () => setHistory([undefined]),
  };
}

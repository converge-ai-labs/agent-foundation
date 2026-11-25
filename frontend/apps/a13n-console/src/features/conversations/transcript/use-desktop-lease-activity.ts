import { useEffect, useEffectEvent, useState, type RefObject } from "react";

// Observe only the connected control surface. Each confirmed expiry starts a new input period.
export function useDesktopLeaseActivity({
  screen,
  expiresAt,
  generation,
  episode,
  renew,
}: {
  screen: RefObject<HTMLDivElement | null>;
  expiresAt: string | null;
  generation?: number;
  episode?: number;
  renew: () => void;
}) {
  const [warning, setWarning] = useState<string | null>(null);
  const requestRenewal = useEffectEvent(renew);

  useEffect(() => {
    const surface = screen.current;
    if (!surface || !expiresAt) return;
    const deadline = Date.parse(expiresAt);
    const remaining = deadline - Date.now();
    if (remaining <= 0) return;
    const remindAt = deadline - Math.min(30_000, remaining / 2);
    let used = false;
    let requested = false;

    function check() {
      if (Date.now() < remindAt || Date.now() >= deadline || document.hidden)
        return;
      if (used && !requested) {
        requested = true;
        requestRenewal();
      } else if (!used) {
        setWarning(expiresAt);
      }
    }

    function input(event: Event) {
      if (
        document.hidden ||
        (event.type === "pointermove" && !(event as PointerEvent).buttons)
      )
        return;
      used = true;
      check();
    }

    const events = ["pointerdown", "pointermove", "keydown", "wheel"];
    for (const name of events) surface.addEventListener(name, input, true);
    document.addEventListener("visibilitychange", check);
    // Very distant deadlines exceed the browser timer range.
    let timer: ReturnType<typeof setTimeout>;
    function schedule() {
      const delay = remindAt - Date.now();
      if (delay > 0)
        timer = setTimeout(schedule, Math.min(delay, 2_147_483_647));
      else check();
    }
    schedule();
    return () => {
      clearTimeout(timer);
      for (const name of events) surface.removeEventListener(name, input, true);
      document.removeEventListener("visibilitychange", check);
      setWarning(null);
    };
  }, [screen, expiresAt, generation, episode]);

  return expiresAt !== null && warning === expiresAt;
}

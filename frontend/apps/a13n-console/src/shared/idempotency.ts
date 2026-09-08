import { useRef } from "react";
/** Keep the same key when a user retries an unchanged command after a lost acknowledgement. */
export function useIdempotency() {
  const last = useRef<{ body: string; key: string } | undefined>(undefined);
  return {
    forBody(body: unknown) {
      const serialized = JSON.stringify(body);
      if (last.current?.body !== serialized)
        last.current = { body: serialized, key: crypto.randomUUID() };
      return last.current.key;
    },
    reset() {
      last.current = undefined;
    },
  };
}

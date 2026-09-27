import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "a13n-ui";
import styles from "./app-card.module.css";

/** App-provided destinations never navigate the authenticated Host or its sandbox. */
export function externalAppLink(
  uri: string,
  hostOrigin: string,
  sandboxUrl: string,
): string {
  if (uri.length > 2048)
    throw new Error("App links must fit within 2048 characters.");
  const url = new URL(uri);
  if (
    !["https:", "http:"].includes(url.protocol) ||
    url.username ||
    url.password ||
    [hostOrigin, new URL(sandboxUrl).origin].includes(url.origin)
  )
    throw new Error(
      "Only external HTTP(S) links without credentials may be opened.",
    );
  return url.href;
}

type PendingLink = {
  href: string;
  resolve: (value: {}) => void;
  reject: (error: Error) => void;
};

export function useAppLink() {
  const pending = useRef<PendingLink | undefined>(undefined);
  const [href, setHref] = useState<string>();
  const cancel = useCallback(() => {
    pending.current?.reject(new Error("The App link was not opened."));
    pending.current = undefined;
    setHref(undefined);
  }, []);
  useEffect(() => cancel, [cancel]);
  const propose = useCallback((uri: string, sandboxUrl: string) => {
    if (pending.current)
      return Promise.reject(new Error("Review the pending App link first."));
    const destination = externalAppLink(
      uri,
      window.location.origin,
      sandboxUrl,
    );
    return new Promise<{}>((resolve, reject) => {
      pending.current = { href: destination, resolve, reject };
      setHref(destination);
    });
  }, []);
  const confirmation = href ? (
    <section
      className={styles.request}
      aria-label="App external link confirmation"
    >
      <strong>Open this external website?</strong>
      <p>
        The App requested this destination. It opens in a new tab without access
        to this conversation.
      </p>
      <pre>{href}</pre>
      <div className={styles.actions}>
        <Button
          size="sm"
          onClick={() => {
            const link = pending.current;
            if (!link) return;
            // Keep window.open in the trusted Host click, not an asynchronous App callback.
            window.open(link.href, "_blank", "noopener,noreferrer");
            pending.current = undefined;
            setHref(undefined);
            link.resolve({});
          }}
        >
          Open external link
        </Button>
        <Button size="sm" variant="ghost" onClick={cancel}>
          Decline link
        </Button>
      </div>
    </section>
  ) : null;
  return { propose, cancel, confirmation };
}

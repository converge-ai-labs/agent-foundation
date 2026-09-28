import { Button, Logo, LogoSpinner, Wordmark } from "a13n-ui";
import styles from "./startup.module.css";

// A public shell, not an optimistic authenticated Workbench. No data consumers
// mount until the listener has validated access and its protocol version.
export function Startup({
  error,
  connecting,
  retry,
}: {
  error: string;
  connecting: boolean;
  retry: () => void;
}) {
  return (
    <main className={styles.shell} aria-busy={connecting}>
      <aside className={styles.sidebar} aria-hidden="true">
        <div className={styles.brand}>
          <Logo alt="" width={28} height={28} />
          <Wordmark />
        </div>
        <div className={styles.placeholder} />
        <div className={styles.placeholder} />
        <div className={styles.placeholder} />
      </aside>
      <section className={styles.content}>
        <header className={styles.header}>Harness UI</header>
        <div className={styles.status}>
          {connecting && <LogoSpinner aria-hidden="true" />}
          <p role="status">{error || "Connecting to Harness UI…"}</p>
          {!connecting && (
            <Button variant="outline" onClick={retry}>
              Retry connection
            </Button>
          )}
        </div>
      </section>
    </main>
  );
}

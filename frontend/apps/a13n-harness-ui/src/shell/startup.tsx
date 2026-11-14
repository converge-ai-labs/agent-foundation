import { LanguageChoice } from "../i18n/language";
import { useTranslation } from "../i18n";
import { Button, Logo } from "a13n-ui";
import shell from "./workbench.module.css";
import styles from "./startup.module.css";

// Skeleton navigation rows, sized like real sidebar labels.
const PLACEHOLDER_WIDTHS = ["44%", "36%", "58%", "48%", "40%"];

// A public shell, not an optimistic authenticated Workbench. No data consumers
// mount until the listener has validated access and its protocol version. It
// reuses the Workbench sidebar geometry so the first real frame does not jump.
export function Startup({
  error,
  connecting,
  retry,
}: {
  error: string;
  connecting: boolean;
  retry: () => void;
}) {
  const { t } = useTranslation();
  return (
    <main className={shell.shell} aria-busy={connecting}>
      <aside className={styles.sidebar} aria-hidden="true">
        <div className={shell.sidebarHeader}>
          <span className={shell.brand}>
            <Logo alt="" width={20} height={20} />
            <span>Harness</span>
          </span>
        </div>
        <div className={styles.rows}>
          {PLACEHOLDER_WIDTHS.map((width, index) => (
            <span key={index} className={styles.row}>
              <span className={styles.bar} style={{ width }} />
            </span>
          ))}
        </div>
      </aside>
      <section className={styles.content}>
        <div className={styles.status}>
          <LanguageChoice />
          {error && <h1>{t("Can't connect to Harness UI")}</h1>}
          <p role="status">{error || t("Connecting to Harness UI…")}</p>
          {!connecting && (
            <Button variant="outline" onClick={retry}>
              {t("Retry connection")}
            </Button>
          )}
        </div>
      </section>
    </main>
  );
}

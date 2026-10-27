import { Button } from "a13n-ui";
import { ArrowClockwise, Monitor } from "@phosphor-icons/react";
import {
  BrowserRequirements,
  BrowserInstallInstructions,
} from "./requirements";
import type { Schema } from "../transport/client";
import styles from "./setup.module.css";

export function BrowserSetup({
  setup,
  loading,
  error,
  check,
  busy,
}: {
  setup?: Schema<"BrowserSetup">;
  loading: boolean;
  error: Error | null;
  check: () => void;
  busy: boolean;
}) {
  const platform =
    setup &&
    {
      linux: "Linux",
      macos: "macOS",
      windows: "Windows",
      other: "Unsupported platform",
    }[setup.platform];
  return (
    <div className={styles.setup} aria-label="Browser preparation">
      <div className={styles.content}>
        <header className={styles.heading}>
          <Monitor size={28} />
          <div>
            <h3>
              {setup && !setup.supported
                ? "Browser unavailable"
                : "Browser setup"}
            </h3>
            <p>
              {setup?.distribution ||
                (platform ? `${platform} Host` : "Checking the Host")}
            </p>
          </div>
        </header>
        <p className={styles.summary}>
          {setup?.message || "Checking browser components…"}
        </p>
        {error && (
          <p role="alert" className={styles.error}>
            {error.message}
          </p>
        )}
        {setup && (
          <BrowserRequirements requirements={setup.requirements ?? []} />
        )}
        <BrowserInstallInstructions steps={setup?.steps ?? []} />
      </div>
      <div className={styles.actions}>
        <div className={styles.buttons}>
          <Button variant="default" disabled={busy || loading} onClick={check}>
            <ArrowClockwise />
            {loading ? "Checking…" : "Check again"}
          </Button>
        </div>
        <a href="/settings#browser">
          Scan and select programs in Browser settings
        </a>
      </div>
    </div>
  );
}

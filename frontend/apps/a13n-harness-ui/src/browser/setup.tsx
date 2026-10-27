import { Button } from "a13n-ui";
import { ArrowClockwise } from "@phosphor-icons/react";
import {
  BrowserRequirements,
  BrowserInstallInstructions,
} from "./requirements";
import type { Schema } from "../transport/client";
import styles from "./setup.module.css";
import { MacCaptureSettings } from "./macos-setup";

export function BrowserSetup({
  setup,
  loading,
  error,
  retry,
}: {
  setup?: Schema<"BrowserSetup">;
  loading: boolean;
  error: string;
  retry: () => void;
}) {
  const unsupported = setup?.supported === false;
  const missing = (setup?.requirements ?? []).filter(
    (item) => !item.installed && item.required !== false,
  );
  const requirements = (setup?.requirements ?? []).filter(
    (item) =>
      missing.includes(item) ||
      (setup?.platform === "macos" && item.name === "Window preview"),
  );
  return (
    <div className={styles.setup} aria-label="Browser troubleshooting">
      <div className={styles.content}>
        <header className={styles.heading}>
          <h3>
            {unsupported
              ? "Linux or macOS Host required"
              : "Browser needs attention"}
          </h3>
        </header>
        <p className={styles.summary} role="alert">
          {unsupported || missing.length
            ? setup?.message
            : error || "Browser could not start."}
        </p>
        {loading ? (
          <p className={styles.summary} role="status">
            Checking components…
          </p>
        ) : (
          !unsupported && (
            <>
              {requirements.length > 0 && (
                <BrowserRequirements
                  requirements={requirements}
                  controls={
                    setup?.platform === "macos"
                      ? {
                          "Window preview": <MacCaptureSettings />,
                        }
                      : {}
                  }
                />
              )}
              <BrowserInstallInstructions steps={setup?.steps ?? []} />
            </>
          )
        )}
        {!unsupported && (
          <div className={styles.recovery}>
            <Button variant="outline" disabled={loading} onClick={retry}>
              <ArrowClockwise />
              Try again
            </Button>
            <a href="/settings#browser">Advanced browser settings</a>
          </div>
        )}
      </div>
    </div>
  );
}

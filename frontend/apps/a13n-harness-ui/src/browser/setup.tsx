import { useState } from "react";
import { Button } from "a13n-ui";
import {
  ArrowClockwise,
  Check,
  CheckCircle,
  CircleDashed,
  Copy,
  Monitor,
} from "@phosphor-icons/react";
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
  const [copied, setCopied] = useState("");
  const [copyError, setCopyError] = useState("");
  const copy = async (title: string, command: string) => {
    try {
      await navigator.clipboard.writeText(command);
      setCopied(title);
      setCopyError("");
    } catch {
      setCopyError(
        "Clipboard access is unavailable. Select and copy the command below.",
      );
    }
  };
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
                ? "Browser support on this platform"
                : "Set up your browser"}
            </h3>
            <p>
              {setup?.distribution ||
                (platform ? `${platform} Host` : "Checking the Host")}
            </p>
          </div>
        </header>
        <p className={styles.summary}>
          {setup?.message ||
            "Checking the optional browser components on the Host running Harness UI…"}
        </p>
        {error && (
          <p role="alert" className={styles.error}>
            {error.message}
          </p>
        )}
        {!!setup?.requirements?.length && (
          <ul className={styles.requirements} aria-label="Browser requirements">
            {setup.requirements?.map((requirement) => (
              <li key={requirement.name}>
                {requirement.installed ? (
                  <CheckCircle size={19} />
                ) : (
                  <CircleDashed size={19} />
                )}
                <div>
                  <strong>{requirement.name}</strong>
                  <span>{requirement.detail}</span>
                </div>
                <span className={styles.state}>
                  {requirement.installed
                    ? "Installed"
                    : requirement.required === false
                      ? "Optional"
                      : "Missing"}
                </span>
              </li>
            ))}
          </ul>
        )}
        {!!setup?.steps?.length && (
          <details className={styles.instructions} open>
            <summary>Manual installation commands</summary>
            <p>
              Run these commands in the Harness UI environment. For Docker,
              installation applies to this container; add the components to your
              image to keep them when the container is replaced. System packages
              may require administrator access.
            </p>
            {setup.steps?.map((step) => (
              <section key={step.title} className={styles.step}>
                <div>
                  <strong>{step.title}</strong>
                  <Button
                    variant="ghost"
                    size="sm"
                    aria-label={`Copy ${step.title} command`}
                    onClick={() => void copy(step.title, step.command)}
                  >
                    {copied === step.title ? <Check /> : <Copy />}
                    {copied === step.title ? "Copied" : "Copy"}
                  </Button>
                </div>
                <pre>
                  <code>{step.command}</code>
                </pre>
              </section>
            ))}
            {copyError && (
              <p role="alert" className={styles.error}>
                {copyError}
              </p>
            )}
          </details>
        )}
      </div>
      <div className={styles.actions}>
        <div className={styles.buttons}>
          <Button variant="default" disabled={busy || loading} onClick={check}>
            <ArrowClockwise />
            {loading ? "Checking…" : "Check again"}
          </Button>
        </div>
        <a href="/settings#browser">Configure browser and VNC</a>
      </div>
    </div>
  );
}

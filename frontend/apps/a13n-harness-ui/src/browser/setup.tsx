import { useEffect, useRef, useState } from "react";
import { Button } from "a13n-ui";
import {
  ArrowClockwise,
  Check,
  CheckCircle,
  CircleDashed,
  Copy,
  DownloadSimple,
} from "@phosphor-icons/react";
import type { Schema } from "../transport/client";
import styles from "./setup.module.css";

export function BrowserSetup({
  setup,
  loading,
  error,
  check,
  install,
  busy,
}: {
  setup?: Schema<"BrowserSetup">;
  loading: boolean;
  error: Error | null;
  check: () => void;
  install: () => void;
  busy: boolean;
}) {
  const installation = setup?.installation;
  const installing = installation?.status === "installing";
  const output = useRef<HTMLPreElement>(null);
  useEffect(() => {
    if (installing && output.current)
      output.current.scrollTop = output.current.scrollHeight;
  }, [installation?.output, installing]);
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
    <div className={styles.setup} aria-label="Browser installation">
      <div className={styles.content}>
        <header className={styles.heading}>
          <DownloadSimple size={28} />
          <div>
            <h3>
              {setup && !setup.supported
                ? "Browser support on this platform"
                : installing
                  ? "Installing browser support"
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
        {installation?.error && (
          <p role="alert" className={styles.error}>
            {installation.error}
          </p>
        )}
        {installing && (
          <p role="status" className={styles.progress}>
            {installation?.step || "Preparing installation…"}
          </p>
        )}
        {installation?.output && (
          <details
            className={styles.logs}
            open={installing || installation.status === "failed"}
          >
            <summary>Installation output</summary>
            <pre ref={output} aria-label="Browser installation output">
              {installation.output}
            </pre>
          </details>
        )}
        {!installing && !!setup?.requirements?.length && (
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
                  {requirement.installed ? "Installed" : "Missing"}
                </span>
              </li>
            ))}
          </ul>
        )}
        {!!setup?.steps?.length && (
          <details
            className={styles.instructions}
            open={!setup?.installable && !installing}
          >
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
          {(setup?.installable || installing) && (
            <Button
              disabled={busy || loading || installing}
              loading={installing}
              onClick={install}
            >
              <DownloadSimple />
              {installing
                ? "Installing…"
                : installation?.status === "failed"
                  ? "Retry installation"
                  : "Install browser support"}
            </Button>
          )}
          <Button
            variant={setup?.installable ? "ghost" : "default"}
            disabled={busy || loading || installing}
            onClick={check}
          >
            <ArrowClockwise />
            {loading ? "Checking…" : "Check again"}
          </Button>
        </div>
        {setup?.supported && (
          <p>
            {setup.install_message ||
              "Once the components are installed, check again to enable the browser."}
          </p>
        )}
      </div>
    </div>
  );
}

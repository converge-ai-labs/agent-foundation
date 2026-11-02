import { useTranslation } from "../i18n";
import { useState, type ReactNode } from "react";
import { Button } from "a13n-ui";
import { Check, CheckCircle, CircleDashed, Copy } from "@phosphor-icons/react";
import type { Schema } from "../transport/client";
import styles from "./setup.module.css";

export function BrowserRequirements({
  requirements,
  controls = {},
  status,
}: {
  requirements: Schema<"BrowserRequirement">[];
  controls?: Partial<Record<string, ReactNode>>;
  status?: "Checking…" | "Not checked";
}) {
  const { t } = useTranslation();
  return (
    <ul className={styles.requirements} aria-label={t("Browser requirements")}>
      {requirements
        .filter((requirement) => !requirement.managed)
        .map((requirement) => (
          <li
            key={requirement.name}
            data-missing={
              !status &&
              !requirement.installed &&
              requirement.required !== false
            }
          >
            {!status && requirement.installed ? (
              <CheckCircle size={19} />
            ) : (
              <CircleDashed size={19} />
            )}
            <div>
              <div className={styles.requirementHeading}>
                <strong>
                  {t(requirement.name)}
                  <small className={styles.requirementKind}>
                    {requirement.required === false
                      ? t("Optional")
                      : t("Required")}
                  </small>
                </strong>
                {(status || !requirement.installed) && (
                  <span className={styles.state}>
                    {status
                      ? t(status)
                      : requirement.required === false
                        ? t("Not installed")
                        : t("Missing")}
                  </span>
                )}
              </div>
              {requirement.purpose && <span>{t(requirement.purpose)}</span>}
              {requirement.detail &&
                (!controls[requirement.name] || !requirement.installed) && (
                  <span>{t(requirement.detail)}</span>
                )}
              {controls[requirement.name] && (
                <div className={styles.requirementControl}>
                  {controls[requirement.name]}
                </div>
              )}
            </div>
          </li>
        ))}
    </ul>
  );
}

export function BrowserInstallInstructions({
  steps,
}: {
  steps: Schema<"BrowserInstallStep">[];
}) {
  const { t } = useTranslation();
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
  if (!steps.length) return null;
  return (
    <details className={styles.instructions}>
      <summary>{t("Manual installation commands")}</summary>
      <p>
        {t(
          "Run these commands on the Host running Harness UI. In Docker, install in the container or image. System packages may require administrator access. Optional components are not needed for basic browsing.",
        )}
      </p>
      {steps.map((step) => (
        <section key={step.title} className={styles.step}>
          <div>
            <strong>{t(step.title)}</strong>
            <Button
              variant="ghost"
              size="sm"
              aria-label={t("Copy {{title}} command", { title: t(step.title) })}
              onClick={() => void copy(step.title, step.command)}
            >
              {copied === step.title ? <Check /> : <Copy />}
              {copied === step.title ? t("Copied") : t("Copy")}
            </Button>
          </div>
          <pre>
            <code>{step.command}</code>
          </pre>
        </section>
      ))}
      {copyError && (
        <p role="alert" className={styles.error}>
          {t(copyError)}
        </p>
      )}
    </details>
  );
}

import { ArrowSquareOutIcon, ShieldWarningIcon } from "@phosphor-icons/react";
import { Alert, AlertDescription, AlertTitle, Button } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "../i18n";
import { copyText } from "../platform";
import { readPreference, writePreference } from "../shell/preferences";
import { SettingsRow, SettingsSection } from "../shell/settings";
import type { Schema } from "../transport/client";
import { useUpdateNotices } from "../transport/context";
import styles from "./update-notices.module.css";

const upgradeCommand = "a13n-harness-ui update";
const updateBadgeKey = "dismissed-update-version";

export function useUpdateNoticeState() {
  const { t } = useTranslation();
  const { data } = useUpdateNotices();
  const [dismissedVersion, setDismissedVersion] = useState(() =>
    readPreference(updateBadgeKey, ""),
  );
  const [hidden, setHidden] = useState<Record<string, string>>({});
  const latest = data?.latest_stable?.is_newer ? data.latest_stable : null;
  const hasUnreadUpdate =
    latest !== null && dismissedVersion !== latest.version;
  const visibleAdvisories = (data?.advisories ?? []).flatMap((advisory) => {
    const key = `advisory.${advisory.id}`;
    const revision = JSON.stringify([
      data?.current_version,
      advisory.updated_at,
      advisory.severity,
      advisory.summary,
    ]);
    return (hidden[key] ?? readPreference(key, "")) === revision
      ? []
      : [{ advisory, key, revision }];
  });
  return {
    currentVersion: data?.current_version,
    installMethod: data?.install_method,
    latest,
    hasUnreadUpdate,
    visibleAdvisories,
    indicator: visibleAdvisories.length
      ? {
          tone: "warning",
          description: t("Security advisory for this installation"),
        }
      : hasUnreadUpdate
        ? { tone: "update", description: t("Harness UI update available") }
        : null,
    toggleUpdateBadge() {
      if (!latest) return;
      const value = hasUnreadUpdate ? latest.version : "";
      writePreference(updateBadgeKey, value);
      setDismissedVersion(value);
    },
    dismissAdvisory(key: string, revision: string) {
      writePreference(key, revision);
      setHidden((current) => ({ ...current, [key]: revision }));
    },
  };
}

export type UpdateNoticeState = ReturnType<typeof useUpdateNoticeState>;

export function UpdateNoticeIndicator({
  indicator,
}: {
  indicator: UpdateNoticeState["indicator"];
}) {
  return indicator ? (
    <span
      className={styles.dot}
      data-tone={indicator.tone}
      aria-hidden="true"
    />
  ) : null;
}

export function UpdateNotices({
  notices,
  installedVersion,
  disconnected,
}: {
  notices: UpdateNoticeState;
  installedVersion: string;
  disconnected: boolean;
}) {
  const { t } = useTranslation();
  const { latest } = notices;
  return (
    <SettingsSection title={t("About")} variant="plain">
      <div className={styles.about}>
        <SettingsSection>
          <SettingsRow
            label={t("Installed version")}
            description={
              <>
                v{notices.currentVersion ?? installedVersion}
                {" · "}
                {disconnected ? t("Reconnecting…") : t("Connected")}
              </>
            }
          >
            {latest && (
              <Button
                variant="ghost"
                size="sm"
                className={styles.releaseLink}
                render={
                  <a href={latest.url} target="_blank" rel="noreferrer" />
                }
              >
                {t("v{{version}} available", { version: latest.version })}
                <ArrowSquareOutIcon aria-hidden="true" />
              </Button>
            )}
          </SettingsRow>
          {latest && (
            <SettingsRow
              label={t("Upgrade")}
              description={
                notices.installMethod === "uv_tool"
                  ? t("Stop Harness UI, then run this command.")
                  : t("Update with the original package manager.")
              }
              className={styles.upgradeRow}
            >
              {notices.installMethod === "uv_tool" && <UpgradeCommand />}
            </SettingsRow>
          )}
          {latest && (
            <SettingsRow
              label={t("Update badge")}
              description={t("The release link stays available here.")}
            >
              <Button
                variant="ghost"
                size="sm"
                onClick={notices.toggleUpdateBadge}
              >
                {notices.hasUnreadUpdate ? t("Hide badge") : t("Show badge")}
              </Button>
            </SettingsRow>
          )}
        </SettingsSection>
        {notices.visibleAdvisories.map(({ advisory, key, revision }) => (
          <AdvisoryNotice
            key={advisory.id}
            advisory={advisory}
            hide={() => notices.dismissAdvisory(key, revision)}
          />
        ))}
      </div>
    </SettingsSection>
  );
}

function UpgradeCommand() {
  const { t } = useTranslation();
  const [feedback, setFeedback] = useState<"copied" | "error" | null>(null);
  return (
    <div className={styles.commandGroup}>
      <div className={styles.command}>
        <code>{upgradeCommand}</code>
        <Button
          variant="ghost"
          size="sm"
          aria-label={t("Copy command")}
          onClick={async () => {
            try {
              await copyText(upgradeCommand);
              setFeedback("copied");
            } catch {
              setFeedback("error");
            }
          }}
        >
          <span aria-live="polite">
            {feedback === "copied" ? t("Copied") : t("Copy")}
          </span>
        </Button>
      </div>
      {feedback === "error" && (
        <p role="status">
          {t("Could not copy. Try again or select the text to copy manually.")}
        </p>
      )}
    </div>
  );
}

function AdvisoryNotice({
  advisory,
  hide,
}: {
  advisory: Schema<"SecurityAdvisory">;
  hide: () => void;
}) {
  const { t } = useTranslation();
  return (
    <Alert variant="warning">
      <ShieldWarningIcon aria-hidden="true" />
      <AlertTitle>
        {advisory.severity === "critical"
          ? t("Critical security advisory")
          : t("High severity security advisory")}
      </AlertTitle>
      <AlertDescription>
        <p className={styles.summary}>{advisory.summary}</p>
        <div className={styles.advisoryActions}>
          <Button
            variant="ghost"
            size="sm"
            render={<a href={advisory.url} target="_blank" rel="noreferrer" />}
          >
            {t("View advisory on GitHub")}
            <ArrowSquareOutIcon aria-hidden="true" />
          </Button>
          <Button variant="ghost" size="sm" onClick={hide}>
            {t("Hide on this browser")}
          </Button>
        </div>
      </AlertDescription>
    </Alert>
  );
}

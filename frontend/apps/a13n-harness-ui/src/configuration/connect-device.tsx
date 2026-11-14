import { useTranslation } from "../i18n";
import { Trans } from "react-i18next";
import {
  SettingsChoice as ChoiceField,
  SettingsSection,
  SettingsToggle,
} from "../shell/settings";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { HelpTip } from "../shell/help-tip";
import { NewResourceButton } from "./sources";
import { useDeviceInfo } from "./device-directory";

import { connectCommand } from "./device-command";
import { copyText } from "../platform";
import layout from "../shell/workbench.module.css";
import settingsLayout from "./settings.module.css";
import styles from "./connect-device.module.css";

export function ConnectDevice({
  onConnected,
  label,
}: {
  onConnected?: (device: Schema<"DeviceSummary">) => void;
  label?: string;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <ModalFrame
      bottomStickOnMobile
      open={open}
      onOpenChange={setOpen}
      title={t("Connect device")}
      closeLabel={t("Close dialog")}
      trigger={
        <Button type="button" variant={onConnected ? "outline" : "default"}>
          {label ?? t("Connect device")}
        </Button>
      }
      footer={
        <Button variant="outline" onClick={() => setOpen(false)}>
          {t("Close")}
        </Button>
      }
    >
      {open && (
        <ConnectDeviceSteps
          onConnected={(device) => {
            onConnected?.(device);
            setOpen(false);
          }}
          inline={!!onConnected}
        />
      )}
    </ModalFrame>
  );
}

function ConnectDeviceSteps({
  onConnected,
  inline,
}: {
  onConnected: (device: Schema<"DeviceSummary">) => void;
  inline: boolean;
}) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const [shell, setShell] = useState<"posix" | "powershell">("posix");
  const [execution, setExecution] = useState(false);
  const [desktop, setDesktop] = useState(false);
  const [approved, setApproved] = useState<Schema<"DeviceSummary">>();
  const [copied, setCopied] = useState("");
  const pairings = useQuery({
    queryKey: ["device-pairings"],
    queryFn: ({ signal }) =>
      result(client.GET("/api/device-pairings", { signal })),
    refetchInterval: 2000,
    enabled: !approved,
  });
  const info = useDeviceInfo(approved?.id ?? "", true, true);
  const command = connectCommand(
    window.location.origin,
    shell,
    execution,
    desktop,
  );
  const copy = useMutation({
    mutationFn: () => copyText(command),
    onSuccess: () => setCopied(command),
  });
  return (
    <div className={styles.steps}>
      {!approved ? (
        <>
          <section className={styles.step}>
            <h3>
              {t("1. Run on the device")}{" "}
              <HelpTip label={t("Installation help")}>
                {t(
                  "Install a13n-envd and add it to PATH on the other computer.",
                )}
              </HelpTip>
            </h3>
            <p>
              {t(
                "Run this command on the other computer and keep its terminal open.",
              )}
            </p>
            <SettingsSection>
              <ChoiceField
                label={t("Device terminal")}
                value={shell}
                onValueChange={(value) => setShell(value as typeof shell)}
                options={[
                  { value: "posix", label: t("macOS / Linux - POSIX shell") },
                  { value: "powershell", label: t("Windows - PowerShell") },
                ]}
              />
              <SettingsToggle
                label={t("Enable shell execution")}
                description={t(
                  "Run commands as the device user, without a sandbox. Server permissions don't apply.",
                )}
                checked={execution}
                onCheckedChange={setExecution}
              />
              <details className={settingsLayout.groupDisclosure}>
                <summary>{t("Desktop access")}</summary>
                <div>
                  <SettingsToggle
                    label={t("Enable desktop observation and control")}
                    description={t(
                      "Requires desktop permission on the device, separately from shell access. Screenshots may be sent to the model and saved in chat history.",
                    )}
                    checked={desktop}
                    onCheckedChange={setDesktop}
                  />
                </div>
              </details>
            </SettingsSection>
            <div className={styles.commandBlock}>
              <div className={styles.commandHeader}>
                <span>{t("Run command")}</span>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => copy.mutate()}
                  loading={copy.isPending}
                >
                  {copied === command ? t("Copied") : t("Copy command")}
                </Button>
              </div>
              <pre className={styles.command}>
                <code>{command}</code>
              </pre>
            </div>
            <ErrorNotice error={copy.error} />
            <details className={layout.details}>
              <summary>{t("Connection help")}</summary>
              <p>
                {t(
                  "Use an address the device can reach and HTTPS for remote connections. Localhost means the device itself.",
                )}
              </p>
              <p>
                {t(
                  "After a restart, run the same command with the same environment variables and access options. Saved credentials are reused unless revoked.",
                )}
              </p>
              <p>
                <Trans
                  i18nKey="For another Host, run a separate envd process with a different <instance>--instance</instance>. Use <host>--host</host> to name the saved connection."
                  components={{ instance: <code />, host: <code /> }}
                />
              </p>
            </details>
          </section>
          <section className={styles.step}>
            <h3>{t("2. Verify and approve")}</h3>
            <p>
              {t(
                "Match the code in the device's terminal before approving. Closing this dialog leaves the request pending.",
              )}
            </p>
            <ErrorNotice error={pairings.error} />
            {!pairings.data?.length && (
              <div className={styles.waiting}>
                <p role="status">{t("Waiting for a connection request…")}</p>
                <HelpTip label={t("About connection requests")}>
                  {t(
                    "Requests disappear if they expire or are handled elsewhere. Check the device's status to confirm approval.",
                  )}
                </HelpTip>
              </div>
            )}
            {pairings.data?.map((pairing) => (
              <PendingDevice
                key={pairing.pairing_id}
                pairing={pairing}
                onApproved={setApproved}
              />
            ))}
          </section>
          <details className={layout.details}>
            <summary>{t("Connect manually")}</summary>
            <p>
              {t(
                "Use an existing envd connection or your own credentials. Return to the device picker after saving.",
              )}
            </p>
            <NewResourceButton kind="device" label={t("Configure manually")} />
          </details>
        </>
      ) : (
        <section className={styles.step}>
          <h3>3. {approved.name}</h3>
          <p role="status">
            {info.data?.available
              ? t("Device online")
              : t("Approved - waiting for Device connection")}
          </p>
          <ErrorNotice error={info.error} />
          {info.data && !info.data.available && (
            <p>
              {t(
                "Keep envd running and check its terminal. Approval saves the device; it still needs to connect.",
              )}
            </p>
          )}
          <p>
            {inline
              ? t(
                  "This device is saved for this server. Closing this dialog keeps it but does not save an environment binding.",
                )
              : t(
                  "This device is saved for this server. Closing this dialog keeps it.",
                )}
          </p>
          <Button
            type="button"
            disabled={!info.data?.available}
            onClick={() => onConnected(approved)}
          >
            {inline ? t("Choose a directory") : t("Done")}
          </Button>
        </section>
      )}
    </div>
  );
}

export function PendingDevice({
  pairing,
  onApproved,
}: {
  pairing: Schema<"PairingChallenge">;
  onApproved?: (device: Schema<"DeviceSummary">) => void;
}) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const cache = useQueryClient();
  const decision = useMutation({
    mutationFn: async (approve: boolean) => {
      const params = { path: { pairing_id: pairing.pairing_id } };
      if (approve)
        return result(
          client.POST("/api/device-pairings/{pairing_id}/approve", { params }),
        );
      await client.POST("/api/device-pairings/{pairing_id}/reject", { params });
      return undefined;
    },
    onSuccess: (device) => {
      void cache.invalidateQueries();
      if (device) onApproved?.(device);
    },
  });
  return (
    <div className="rounded-xl bg-muted p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <strong>{pairing.name}</strong>
          <p className="text-sm text-muted-foreground">{pairing.device_id}</p>
          <p>
            {t("Verification code:")} <code>{pairing.verification_code}</code>
          </p>
          <p className="text-xs text-muted-foreground">
            {t("Expires {{time}}", {
              time: new Date(pairing.expires_at).toLocaleTimeString(),
            })}
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            type="button"
            variant="ghost"
            disabled={decision.isPending}
            onClick={() => decision.mutate(false)}
          >
            {t("Reject")}
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={decision.isPending}
            onClick={() => decision.mutate(true)}
          >
            {t("Approve Device")}
          </Button>
        </div>
      </div>
      <ErrorNotice error={decision.error} />
    </div>
  );
}

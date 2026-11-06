import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowClockwise } from "@phosphor-icons/react";
import {
  Button,
  StatusPill,
  Tooltip,
  TooltipPopup,
  TooltipTrigger,
} from "a13n-ui";
import { useTranslation } from "../i18n";
import { useTransport } from "../transport/context";
import { result } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { SettingsRow, SettingsSection } from "../shell/settings";
import { BrowserFields } from "./browser-fields";
import { readDocument } from "./documents";
import layout from "./settings.module.css";

export function BrowserConfigurationFields({
  source,
  savedSource,
  onChange,
}: {
  source: string;
  savedSource: string;
  onChange: (value: string) => void;
}) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const queries = useQueryClient();
  const runtime = useQuery({
    queryKey: ["browser-runtime"],
    queryFn: ({ signal }) => result(client.GET("/api/browser", { signal })),
    refetchInterval: 2000,
  });
  const prepare = useMutation({
    mutationFn: () => result(client.POST("/api/browser/prepare")),
    onSuccess: (state) => {
      queries.setQueryData(["browser-runtime"], state);
      void queries.invalidateQueries({ queryKey: ["browser"] });
    },
  });
  const state = runtime.data;
  const saved = readDocument(savedSource);
  const effective = state?.configuration;
  const restartRequired =
    effective &&
    ((saved?.getIn(["browser", "mode"]) ?? "auto") !== effective.mode ||
      (saved?.getIn(["browser", "executable"]) ?? null) !==
        effective.executable ||
      (saved?.getIn(["browser", "vnc_executable"]) ?? null) !==
        effective.vnc_executable);
  const labels = {
    disabled: "Disabled",
    starting: "Starting…",
    ready: "Ready",
    failed: "Failed",
  };
  return (
    <>
      <SettingsSection title={t("Host browser")}>
        <SettingsRow
          label={t("Runtime")}
          description={t("Runs continue when the browser is unavailable.")}
        >
          <div className="flex items-center gap-2">
            <StatusPill
              variant={
                state?.status === "ready"
                  ? "success"
                  : state?.status === "failed"
                    ? "danger"
                    : "neutral"
              }
            >
              {prepare.isPending
                ? t("Starting…")
                : state?.status
                  ? t(labels[state.status])
                  : t("Loading…")}
            </StatusPill>
            <Tooltip>
              <TooltipTrigger
                render={
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label={t("Retry browser startup")}
                    disabled={prepare.isPending || state?.status !== "failed"}
                    onClick={() => prepare.mutate()}
                  />
                }
              >
                <ArrowClockwise />
              </TooltipTrigger>
              <TooltipPopup>{t("Retry browser startup")}</TooltipPopup>
            </Tooltip>
          </div>
        </SettingsRow>
        {state?.error && (
          <p className={layout.hint} role="status">
            {state.status === "disabled" ? t(state.error) : state.error}
          </p>
        )}
        {restartRequired && (
          <p className={layout.hint} role="status">
            {t(
              "Saved browser settings require an App restart. Retry uses the active configuration.",
            )}
          </p>
        )}
        <ErrorNotice error={runtime.error || prepare.error} />
        {effective && (
          <details className={layout.groupDisclosure}>
            <summary>{t("Active configuration")}</summary>
            <div>
              <SettingsRow label={t("Browser mode")}>
                {t(effective.mode === "auto" ? "Auto" : "Disabled")}
              </SettingsRow>
              <SettingsRow label={t("Browser executable")}>
                {effective.executable || t("Automatic")}
              </SettingsRow>
              {state?.platform === "linux" && (
                <SettingsRow label={t("VNC executable")}>
                  {effective.vnc_executable || t("Automatic")}
                </SettingsRow>
              )}
            </div>
          </details>
        )}
      </SettingsSection>
      <BrowserFields
        source={source}
        onChange={onChange}
        active={!!state?.available}
      />
    </>
  );
}

import { ArrowSquareOutIcon, ShieldWarningIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import {
  Alert,
  AlertAction,
  AlertDescription,
  AlertTitle,
  Button,
  SettingsRow,
  SettingsSection,
} from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useAuth, useClient } from "../../auth/context";
import { data, type Schema } from "../../shared/api";

export function UpdateNotices({ organizationId }: { organizationId: string }) {
  const { t } = useTranslation();
  const client = useClient();
  const userId = useAuth().data?.user.value.id;
  const query = useQuery({
    queryKey: ["update-notices", organizationId],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/organizations/{organization_id}/update-notices", {
          params: { path: { organization_id: organizationId } },
          signal,
        })
        .then(data),
  });
  if (!userId || query.isError || !query.data) return null;
  const { current_version, latest_stable, advisories } = query.data;
  const hasUpdate = latest_stable?.is_newer;
  return (
    <>
      {advisories.map((advisory) => {
        const storageKey = `a13n-console:advisory:${userId}:${organizationId}:${advisory.id}`;
        const revision = JSON.stringify([
          current_version,
          advisory.updated_at,
          advisory.severity,
          advisory.summary,
        ]);
        return (
          <AdvisoryNotice
            key={JSON.stringify([storageKey, revision])}
            advisory={advisory}
            storageKey={storageKey}
            revision={revision}
          />
        );
      })}
      {hasUpdate && (
        <SettingsSection variant="outlined" title={t("Service version")}>
          <SettingsRow
            label={t("Installed version")}
            description={`v${current_version}`}
          >
            <Button
              variant="ghost"
              size="sm"
              render={
                <a href={latest_stable.url} target="_blank" rel="noreferrer" />
              }
            >
              {t("v{{version}} available", { version: latest_stable.version })}
              <ArrowSquareOutIcon aria-hidden="true" />
            </Button>
          </SettingsRow>
        </SettingsSection>
      )}
    </>
  );
}

function AdvisoryNotice({
  advisory,
  storageKey,
  revision,
}: {
  advisory: Schema["SecurityAdvisory"];
  storageKey: string;
  revision: string;
}) {
  const { t } = useTranslation();
  const [hidden, setHidden] = useState(() => {
    try {
      return localStorage.getItem(storageKey) === revision;
    } catch {
      return false;
    }
  });
  if (hidden) return null;
  return (
    <Alert variant="warning">
      <ShieldWarningIcon aria-hidden="true" />
      <AlertTitle>
        {advisory.severity === "critical"
          ? t("Critical security advisory")
          : t("High-severity security advisory")}
      </AlertTitle>
      <AlertDescription className="min-w-0 break-words">
        <p>{advisory.summary}</p>
        <a
          className="w-fit inline-flex items-center gap-1 text-foreground underline underline-offset-2"
          href={advisory.url}
          target="_blank"
          rel="noreferrer"
        >
          {t("View advisory on GitHub")}
          <ArrowSquareOutIcon size={13} aria-hidden="true" />
        </a>
      </AlertDescription>
      <AlertAction>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={() => {
            try {
              localStorage.setItem(storageKey, revision);
            } catch {
              // Browser storage may be unavailable; still hide it for this visit.
            }
            setHidden(true);
          }}
        >
          {t("Hide on this browser")}
        </Button>
      </AlertAction>
    </Alert>
  );
}

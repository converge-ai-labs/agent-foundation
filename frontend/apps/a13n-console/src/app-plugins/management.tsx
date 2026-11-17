import { Button, SettingsRow, SettingsSection, Switch } from "a13n-ui";
import { PuzzlePieceIcon } from "@phosphor-icons/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../auth/context";
import { useAccess } from "../layout/workspace";
import { data, rowTag } from "../shared/api";
import { Empty } from "../shared/collection";
import { ErrorNotice, ErrorPage, Loading } from "../shared/feedback";
import { catalogKey, useAppCatalog, useAppPlugins } from "./registry";

export function AppManagement() {
  const { t } = useTranslation();
  const { organization, organizationCan } = useAccess();
  const client = useClient();
  const cache = useQueryClient();
  const catalog = useAppCatalog();
  const reloadButton = useRef<HTMLButtonElement>(null);
  const restoreReloadFocus = useRef(false);
  useEffect(() => {
    if (catalog.isFetching || !restoreReloadFocus.current) return;
    restoreReloadFocus.current = false;
    if (document.activeElement === document.body) reloadButton.current?.focus();
  }, [catalog.isFetching]);
  const { apps } = useAppPlugins();
  const update = useMutation({
    mutationFn: ({
      appId,
      enabled,
      etag,
    }: {
      appId: string;
      enabled: boolean;
      etag: string;
    }) =>
      client.http
        .PUT("/api/v1/organizations/{organization_id}/app-plugins/{app_id}", {
          params: {
            path: { organization_id: organization.id, app_id: appId },
            header: { "If-Match": etag },
          },
          body: { enabled },
        })
        .then(data),
    onSuccess: async (inventory) => {
      const queryKey = catalogKey(inventory.id);
      await cache.cancelQueries({ queryKey, exact: true });
      cache.setQueryData(queryKey, inventory);
    },
  });
  if (catalog.isPending) return <Loading />;
  if (catalog.error && !catalog.data)
    return (
      <ErrorPage
        error={catalog.error}
        actions={
          <Button onClick={() => void catalog.refetch()}>
            {t("Try again")}
          </Button>
        }
      />
    );
  if (!catalog.data?.items.length)
    return (
      <Empty
        icon={<PuzzlePieceIcon />}
        title={t("No apps installed")}
        description={t(
          "Install an App package and restart Service to make it available here.",
        )}
      />
    );
  return (
    <div className="space-y-6">
      <p className="text-muted-foreground">
        {t(
          "Enable apps for everyone in this organization. New Runs use the updated selection; active Runs keep theirs.",
        )}
      </p>
      <ErrorNotice
        error={catalog.error ?? update.error}
        description={t(
          catalog.error
            ? "Installed apps could not be reloaded. The last confirmed state is shown."
            : "The App change could not be confirmed. Reload installed apps before changing an App again.",
        )}
      />
      <Button
        ref={reloadButton}
        variant="outline"
        loading={catalog.isFetching}
        disabled={catalog.isFetching || update.isPending}
        onClick={async () => {
          restoreReloadFocus.current =
            document.activeElement === reloadButton.current;
          const fresh = await catalog.refetch();
          if (fresh.isSuccess) update.reset();
        }}
      >
        {t("Reload installed apps")}
      </Button>
      <SettingsSection variant="outlined">
        {catalog.data.items.map((app) => (
          <SettingsRow
            key={app.app_id}
            controlId={`app-plugin-${app.app_id}`}
            label={app.name}
            description={
              <span>
                {app.distribution} · {app.version}
                <br />
                <span className="select-text">{app.app_id}</span>
                {apps.find((loaded) => loaded.app.app_id === app.app_id)
                  ?.error && (
                  <span className="block text-destructive">
                    {t(
                      "Frontend unavailable. Check the installed package and reload Console.",
                    )}
                  </span>
                )}
              </span>
            }
          >
            <Switch
              id={`app-plugin-${app.app_id}`}
              aria-describedby={`app-plugin-${app.app_id}-description`}
              aria-busy={
                update.isPending && update.variables?.appId === app.app_id
              }
              checked={app.enabled}
              disabled={
                !organizationCan("admin") || update.isPending || !!update.error
              }
              onCheckedChange={(enabled) =>
                update.mutate({
                  appId: app.app_id,
                  enabled,
                  etag: rowTag(catalog.data!),
                })
              }
            />
          </SettingsRow>
        ))}
      </SettingsSection>
    </div>
  );
}

import { StopIcon, TrashIcon } from "@phosphor-icons/react";
import { useQueryClient } from "@tanstack/react-query";
import { MenuItem, MenuSeparator } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, ifMatch, type Schema } from "../../shared/api";
import { Confirm } from "../../shared/dialogs";

type Capabilities = Pick<
  Schema["ProviderType"],
  "supports_stop" | "supports_destroy"
>;

export function environmentLifecycleAvailability(
  environment: Schema["EnvironmentView"],
  capabilities?: Capabilities,
) {
  const managed = !!environment.template_id;
  const { status } = environment;
  return {
    canStop:
      managed &&
      !!capabilities?.supports_stop &&
      !["stopping", "stopped", "deleting", "deleted"].includes(status),
    canDelete:
      (!managed || !!capabilities?.supports_destroy) &&
      !["deleting", "deleted"].includes(status),
  };
}

/** The same confirmed lifecycle commands serve the list and inspector. */
export function EnvironmentLifecycleItems({
  environment,
  etag,
  canStop,
  canDelete,
}: {
  environment: Schema["EnvironmentView"];
  etag?: string;
  canStop: boolean;
  canDelete: boolean;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { t } = useTranslation();
  const name = environment.name;
  async function act(action: "stop" | "delete") {
    const http = client.workspace(environment.workspace_id);
    const request = {
      params: { path: { environment_id: environment.id } },
      headers: ifMatch(etag),
    };
    if (action === "stop")
      await http
        .POST("/api/v1/environments/{environment_id}/stop", request)
        .then(data);
    else
      await http
        .DELETE("/api/v1/environments/{environment_id}", request)
        .then(data);
    await Promise.all([
      cache.invalidateQueries({ queryKey: ["environment", environment.id] }),
      cache.invalidateQueries({ queryKey: ["environments"] }),
      cache.invalidateQueries({ queryKey: ["run-options"] }),
    ]);
  }
  return (
    <>
      {canStop && (
        <Confirm
          subject={name}
          title={t("Stop environment")}
          description={t(
            "This stops the environment when no run is using it. A later run can resume it.",
          )}
          triggerElement={
            <MenuItem
              closeOnClick={false}
              disabled={!!environment.failure?.permanent}
            >
              <StopIcon aria-hidden="true" />
              {t("Stop")}
            </MenuItem>
          }
          action={() => act("stop")}
        />
      )}
      {canStop && canDelete && <MenuSeparator />}
      {canDelete && (
        <Confirm
          subject={name}
          title={t("Delete environment")}
          description={t(
            "The environment is retired and cannot be used again. A managed environment is destroyed with its files; a registered device keeps running outside the Service. Environments in use by a run, or mounted by a session while still usable, cannot be deleted.",
          )}
          danger
          triggerElement={
            <MenuItem closeOnClick={false} variant="destructive">
              <TrashIcon aria-hidden="true" />
              {t("Delete")}
            </MenuItem>
          }
          action={() => act("delete")}
        />
      )}
    </>
  );
}

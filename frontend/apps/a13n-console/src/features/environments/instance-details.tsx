import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Button, ReadOnlyField, ModalFrame } from "a13n-ui";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { ResourceIdentity } from "../../shared/collection";
import { CopyableId } from "../../shared/copy";
import {
  ErrorNotice,
  ErrorToast,
  Loading,
  StateBadge,
  Timestamp,
} from "../../shared/feedback";
import { Confirm } from "../../shared/form";
import { useIdempotency } from "../../shared/idempotency";
import { ProviderIcon } from "../../shared/provider-icon";
import styles from "../../shared/shared.module.css";
import { environmentQuery } from "./api";
import { EnvironmentNameEditor } from "./instance-name";

export function EnvironmentDetails({
  environment,
}: {
  environment: Schema["Environment"];
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation(),
    [open, setOpen] = useState(false),
    [nameEditorKey, setNameEditorKey] = useState(0),
    [commandId, setCommandId] = useState<string>(),
    key = useIdempotency();
  const detail = useQuery({
    ...environmentQuery(client, environment.id),
    enabled: open,
  });
  const provider = useQuery({
    queryKey: ["environment-provider", environment.provider_id],
    enabled: open && can("environment_provider.read"),
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/environment-providers/{resource_id}", {
          params: { path: { resource_id: environment.provider_id } },
          signal,
        })
        .then(data),
  });
  const command = useQuery({
    queryKey: ["environment-command", commandId],
    enabled: !!commandId,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/environment-commands/{command_id}", {
          params: { path: { command_id: commandId! } },
          signal,
        })
        .then(data),
    refetchInterval: (query) =>
      query.state.data?.status === "pending" ? 2000 : false,
  });
  useEffect(() => {
    if (!command.data || command.data.status === "pending") return;
    void cache.invalidateQueries({ queryKey: ["environment", environment.id] });
    void cache.invalidateQueries({ queryKey: ["environments"] });
    void cache.invalidateQueries({ queryKey: ["run-options"] });
  }, [cache, environment.id, command.data?.id, command.data?.status]);
  async function act(action: "stop" | "delete") {
    const params = {
      path: { environment_id: environment.id },
      header: commandHeaders(
        workspace.id,
        key.forBody({ action, id: environment.id }),
      ),
    };
    const receipt =
      action === "stop"
        ? data(
            await client.http.POST(
              "/api/v1/environments/{environment_id}/stop",
              { params },
            ),
          )
        : data(
            await client.http.POST(
              "/api/v1/environments/{environment_id}/delete",
              { params },
            ),
          );
    key.reset();
    setCommandId(receipt.id);
  }
  return (
    <ModalFrame
      onOpenChange={setOpen}
      trigger={
        <Button size="sm" variant="outline" type="button">
          {t("Details")}
        </Button>
      }
      size={"md"}
      title={t("Environment details")}
      closeLabel={t("Close")}
      open={open}
    >
      <div className={styles.stack}>
        <ErrorNotice error={detail.error} />
        <ErrorToast error={command.error} />
        {detail.isPending ? (
          <Loading variant="form" rows={5} />
        ) : (
          detail.data && (
            <>
              <ResourceIdentity
                name={detail.data.value.name}
                resourceId={detail.data.value.id}
              />
              {can("environment.manage") && (
                <EnvironmentNameEditor
                  key={nameEditorKey}
                  environment={detail.data.value}
                  etag={detail.data?.etag}
                  reload={async () => {
                    const result = await detail.refetch();
                    if (result.isSuccess)
                      setNameEditorKey((value) => value + 1);
                  }}
                />
              )}
              <div className={styles.twoColumns}>
                <ReadOnlyField label={t("Status")}>
                  <StateBadge state={detail.data.value.status} />
                </ReadOnlyField>
                <ReadOnlyField label={t("Activity")}>
                  <StateBadge state={detail.data.value.retention_condition} />
                </ReadOnlyField>
                <ReadOnlyField label={t("Activity since")}>
                  <Timestamp value={detail.data.value.condition_since} />
                </ReadOnlyField>
                <ReadOnlyField label={t("Generation")}>
                  {detail.data.value.generation}
                </ReadOnlyField>
                <ReadOnlyField label={t("Updated")}>
                  <Timestamp value={detail.data.value.updated_at} />
                </ReadOnlyField>
              </div>
              <RetentionDetails environment={detail.data.value} />
              <div className={`${styles.stack} border-t border-border pt-4`}>
                <ReadOnlyField label={t("Ownership")}>
                  {t(
                    detail.data.value.ownership === "managed"
                      ? "Managed"
                      : "External",
                  )}
                </ReadOnlyField>
                <ReadOnlyField label={t("Provider")}>
                  {provider.data ? (
                    <ResourceIdentity
                      name={provider.data.name}
                      resourceId={provider.data.id}
                      icon={<ProviderIcon type={provider.data.type} />}
                    />
                  ) : (
                    <CopyableId value={detail.data.value.provider_id} />
                  )}
                </ReadOnlyField>
                {detail.data.value.template_revision_id && (
                  <ReadOnlyField label={t("Template revision")}>
                    <CopyableId
                      value={detail.data.value.template_revision_id}
                    />
                  </ReadOnlyField>
                )}
              </div>
            </>
          )
        )}
        {command.data && (
          <div role="status">
            <strong>{t("Lifecycle command")}</strong>{" "}
            <StateBadge state={command.data.status} />
            <small>{command.data.id}</small>
          </div>
        )}
        {can("environment.manage") &&
          (detail.data?.value.supports_stop ||
            detail.data?.value.supports_destroy) && (
            <div className={`${styles.actions} border-t border-border pt-4`}>
              {detail.data.value.supports_stop && (
                <Confirm
                  subject={environment.id}
                  title={t("Stop environment target")}
                  description={t(
                    "This stops the target when it has no active users. A later run can resume it.",
                  )}
                  trigger={t("Stop target")}
                  action={() => act("stop")}
                />
              )}
              {detail.data.value.supports_destroy && (
                <Confirm
                  subject={environment.id}
                  title={t("Delete environment target")}
                  description={t(
                    "Files and processes on the target will be lost. The next use automatically creates a fresh target from the frozen template revision; old files are not restored. Environment identity and history are retained.",
                  )}
                  trigger={t("Delete target")}
                  danger
                  triggerVariant="outline"
                  action={() => act("delete")}
                />
              )}
            </div>
          )}
      </div>
    </ModalFrame>
  );
}

function RetentionDetails({
  environment,
}: {
  environment: Schema["EnvironmentDetail"];
}) {
  const { t, i18n } = useTranslation();
  const policy = environment.retention;
  return (
    <section className={`${styles.stack} border-t border-border pt-4`}>
      <h3>{t("Effective retention policy")}</h3>
      {policy === null ? (
        <p>
          {t(
            "Externally owned: Service does not automatically stop or delete this target.",
          )}
        </p>
      ) : (
        <>
          <p>
            {t(
              "Frozen at allocation. Later template changes do not affect this environment.",
            )}
          </p>
          <div className={styles.twoColumns}>
            {(
              [
                [t("Stop after idle"), policy.idle.stop_after],
                [t("Delete after idle"), policy.idle.delete_after],
              ] as const
            ).map(([label, seconds]) => (
              <ReadOnlyField key={label} label={label}>
                {seconds === null
                  ? t("Disabled")
                  : t("{{seconds}} seconds", {
                      seconds: new Intl.NumberFormat(
                        i18n.resolvedLanguage,
                      ).format(seconds),
                    })}
              </ReadOnlyField>
            ))}
          </div>
          <p>
            {t(
              "Idle time starts when no runs actively use this environment. Stopping does not reset the deletion timer.",
            )}
          </p>
          <p>
            {t(
              "After target deletion, the next use creates a fresh target from the frozen template revision. Old files are not restored.",
            )}
          </p>
        </>
      )}
    </section>
  );
}

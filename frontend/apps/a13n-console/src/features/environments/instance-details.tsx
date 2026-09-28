import { DotsThreeOutlineVerticalIcon } from "@phosphor-icons/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Menu, MenuItem, MenuPopup, MenuTrigger } from "a13n-ui";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, ifMatch, type Schema } from "../../shared/api";
import { Confirm } from "../../shared/dialogs";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { CopyableId, ProviderIcon } from "../../shared/identity";
import { Panel } from "../../shared/page";
import { environmentQuery } from "./api";
import styles from "./environments.module.css";
import { EnvironmentConnectionEditor } from "./instance-connection";
import { EnvironmentNameEditor } from "./instance-name";
import { useEnvironmentTypes } from "./providers";

/**
 * Standalone entry point: a "Details" button that opens the same inspector.
 * Collections open the panel directly from the row instead.
 */
export function EnvironmentDetails({
  environment,
}: {
  environment: Schema["EnvironmentView"];
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        size="sm"
        variant="outline"
        type="button"
        onClick={() => setOpen(true)}
      >
        {t("Details")}
      </Button>
      {open && (
        <EnvironmentPanel
          environment={environment}
          open
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}

/**
 * The inspector for one environment: what it is, how it is retained, and the
 * two lifecycle commands a target may support.
 */
export function EnvironmentPanel({
  environment,
  open,
  onClose,
}: {
  environment: Schema["EnvironmentView"];
  open: boolean;
  onClose: () => void;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { can } = useWorkspace(),
    { t } = useTranslation(),
    [renaming, setRenaming] = useState(false),
    [nameEditorKey, setNameEditorKey] = useState(0),
    [reconnecting, setReconnecting] = useState(false),
    [connectionEditorKey, setConnectionEditorKey] = useState(0);
  const detail = useQuery({
    ...environmentQuery(client, environment.workspace_id, environment.id),
    enabled: open,
    // The outstanding lifecycle operation settles in the background.
    refetchInterval: (query) =>
      query.state.data?.value.operation_id ? 2000 : false,
  });
  // An external target has no provider: it is reached at its own endpoint.
  const provider = useQuery({
    queryKey: ["environment-provider", environment.provider_id],
    enabled: open && can("read") && !!environment.provider_id,
    queryFn: ({ signal }) =>
      client
        .workspace(environment.workspace_id)
        .GET("/api/v1/environment-providers/{provider_id}", {
          params: { path: { provider_id: environment.provider_id! } },
          signal,
        })
        .then(data),
  });
  // The idle policy is the template's current one.
  const template = useQuery({
    queryKey: ["environment-template", environment.template_id],
    enabled: open && !!environment.template_id,
    queryFn: ({ signal }) =>
      client
        .workspace(environment.workspace_id)
        .GET("/api/v1/environment-templates/{template_id}", {
          params: {
            path: {
              template_id: environment.template_id!,
            },
          },
          signal,
        })
        .then(data),
  });
  async function act(action: "stop" | "delete") {
    const http = client.workspace(environment.workspace_id);
    const request = {
      params: { path: { environment_id: environment.id } },
      headers: ifMatch(detail.data?.etag),
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
  const types = useEnvironmentTypes(open);
  const value = detail.data?.value;
  const capabilities = types.data?.items.find(
    (item) => item.type === provider.data?.type,
  );
  // External targets are connect-only: the Service never stops them, and
  // deleting one only retires it, so no provider type decides anything.
  const managed = !!value?.template_id;
  const supportsStop = managed && !!capabilities?.supports_stop;
  const supportsDestroy = !managed || !!capabilities?.supports_destroy;
  const lifecycle =
    can("write") && !!value && (supportsStop || supportsDestroy);
  return (
    <Panel
      open={open}
      onClose={onClose}
      label={t("Environment details")}
      defaultWidth={420}
      title={
        <span className={styles.panelTitle}>
          <strong title={value?.name ?? environment.name}>
            {value?.name ?? environment.name}
          </strong>
          {value && <StatePill state={value.status} />}
        </span>
      }
      actions={
        lifecycle && (
          <Menu>
            <MenuTrigger
              render={
                <Button
                  variant="ghost"
                  size="icon-sm"
                  type="button"
                  aria-label={t("Environment actions")}
                  title={t("Environment actions")}
                />
              }
            >
              <DotsThreeOutlineVerticalIcon size={14} weight="fill" />
            </MenuTrigger>
            <MenuPopup align="end">
              {supportsStop && (
                <Confirm
                  subject={environment.id}
                  title={t("Stop environment target")}
                  description={t(
                    "This stops the target when it has no active users. A later run can resume it.",
                  )}
                  triggerElement={
                    <MenuItem closeOnClick={false}>{t("Stop target")}</MenuItem>
                  }
                  action={() => act("stop")}
                />
              )}
              {supportsDestroy && (
                <Confirm
                  subject={environment.id}
                  title={t("Delete environment target")}
                  description={t(
                    "The environment is retired and cannot be used again. A managed target is destroyed with its files; a registered device keeps running outside the Service. Environments in use by a run, or mounted by a conversation while still usable, cannot be deleted.",
                  )}
                  danger
                  triggerElement={
                    <MenuItem closeOnClick={false} variant="destructive">
                      {t("Delete target")}
                    </MenuItem>
                  }
                  action={() => act("delete")}
                />
              )}
            </MenuPopup>
          </Menu>
        )
      }
    >
      <div className={styles.panelBody}>
        <ErrorNotice error={detail.error} />
        {detail.isPending ? (
          <Loading variant="form" rows={5} />
        ) : (
          value && (
            <>
              <section className={styles.factGroup}>
                <h3>{t("Environment")}</h3>
                <dl className={styles.facts}>
                  <Fact label={t("Name")}>
                    <span>{value.name}</span>
                    {can("write") && !renaming && (
                      <Button
                        type="button"
                        size="sm"
                        variant="ghost"
                        onClick={() => setRenaming(true)}
                      >
                        {t("Rename")}
                      </Button>
                    )}
                  </Fact>
                  {value.device_id && (
                    <Fact label={t("Device ID")}>
                      <CopyableId value={value.device_id} />
                    </Fact>
                  )}
                  <Fact label={t("Activity")}>
                    <Timestamp value={value.last_used_at} />
                  </Fact>
                  <Fact label={t("Updated")}>
                    <Timestamp value={value.updated_at} />
                  </Fact>
                </dl>
                {renaming && can("write") && (
                  <EnvironmentNameEditor
                    key={nameEditorKey}
                    environment={value}
                    etag={detail.data?.etag}
                    onCancel={() => setRenaming(false)}
                    reload={async () => {
                      const result = await detail.refetch();
                      if (result.isSuccess)
                        setNameEditorKey((current) => current + 1);
                    }}
                  />
                )}
              </section>
              <section className={styles.factGroup}>
                <h3>{t("Source")}</h3>
                <dl className={styles.facts}>
                  <Fact label={t("Ownership")}>
                    {t(value.template_id ? "Managed" : "External")}
                  </Fact>
                  {value.endpoint ? (
                    <Fact label={t("Endpoint")}>
                      <CopyableId value={value.endpoint} />
                      {can("write") &&
                        value.status !== "deleted" &&
                        !reconnecting && (
                          <Button
                            type="button"
                            size="sm"
                            variant="ghost"
                            onClick={() => setReconnecting(true)}
                          >
                            {t("Update connection")}
                          </Button>
                        )}
                    </Fact>
                  ) : (
                    value.provider_id && (
                      <Fact label={t("Provider")}>
                        {provider.data ? (
                          <>
                            <ProviderIcon type={provider.data.type} />
                            <span>{provider.data.name}</span>
                          </>
                        ) : (
                          <CopyableId value={value.provider_id} />
                        )}
                      </Fact>
                    )
                  )}
                  {value.template_id && (
                    <Fact label={t("Template")}>
                      <CopyableId value={value.template_id} />
                    </Fact>
                  )}
                </dl>
                {reconnecting && can("write") && (
                  <EnvironmentConnectionEditor
                    key={connectionEditorKey}
                    environment={value}
                    etag={detail.data?.etag}
                    onClose={() => setReconnecting(false)}
                    reload={async () => {
                      const result = await detail.refetch();
                      if (result.isSuccess)
                        setConnectionEditorKey((current) => current + 1);
                    }}
                  />
                )}
              </section>
              <ErrorNotice error={template.error} />
              {(!managed || template.data) && (
                <RetentionDetails policy={template.data?.config ?? null} />
              )}
            </>
          )
        )}
        {(value?.operation_id || value?.failure) && (
          <section className={styles.factGroup} role="status">
            <h3>{t("Lifecycle command")}</h3>
            <dl className={styles.facts}>
              <Fact label={t("Status")}>
                <StatePill state={value.failure ? "failed" : "pending"} />
              </Fact>
              {value.operation_id && (
                <Fact label={t("Command")}>
                  <CopyableId value={value.operation_id} />
                </Fact>
              )}
              {value.failure && (
                <Fact label={t("Error")}>{value.failure.message}</Fact>
              )}
            </dl>
          </section>
        )}
      </div>
    </Panel>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className={styles.fact}>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

/** The template's current idle policy; an external target has none. */
function RetentionDetails({
  policy,
}: {
  policy: Schema["TemplateConfig"] | null;
}) {
  const { t, i18n } = useTranslation();
  return (
    <section className={styles.factGroup}>
      <h3>{t("Effective retention policy")}</h3>
      {policy === null ? (
        <p className={styles.panelNote}>
          {t(
            "Externally owned: Service does not automatically stop or delete this target.",
          )}
        </p>
      ) : (
        <>
          <dl className={styles.facts}>
            {(
              [
                [t("Stop after idle"), policy.stop_after_seconds ?? null],
                [t("Delete after idle"), policy.delete_after_seconds ?? null],
              ] as const
            ).map(([label, seconds]) => (
              <Fact key={label} label={label}>
                {seconds === null
                  ? t("Disabled")
                  : t("{{seconds}} seconds", {
                      seconds: new Intl.NumberFormat(
                        i18n.resolvedLanguage,
                      ).format(seconds),
                    })}
              </Fact>
            ))}
          </dl>
          <div className={styles.panelNotes}>
            <p className={styles.panelNote}>
              {t(
                "Idle time starts when no runs actively use this environment. Stopping does not reset the deletion timer.",
              )}
            </p>
          </div>
        </>
      )}
    </section>
  );
}

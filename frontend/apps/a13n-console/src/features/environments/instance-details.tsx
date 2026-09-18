import { DotsThreeOutlineVerticalIcon } from "@phosphor-icons/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Menu, MenuItem, MenuPopup, MenuTrigger } from "a13n-ui";
import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { Confirm } from "../../shared/dialogs";
import {
  ErrorNotice,
  ErrorToast,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { CopyableId, ProviderIcon } from "../../shared/identity";
import { useIdempotency } from "../../shared/idempotency";
import { Panel } from "../../shared/page";
import { environmentQuery } from "./api";
import styles from "./environments.module.css";
import { EnvironmentNameEditor } from "./instance-name";

/**
 * Standalone entry point: a "Details" button that opens the same inspector.
 * Collections open the panel directly from the row instead.
 */
export function EnvironmentDetails({
  environment,
}: {
  environment: Schema["Environment"];
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
  environment: Schema["Environment"];
  open: boolean;
  onClose: () => void;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation(),
    [renaming, setRenaming] = useState(false),
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
  const value = detail.data?.value;
  const lifecycle =
    can("environment.manage") &&
    (value?.supports_stop || value?.supports_destroy);
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
              {value?.supports_stop && (
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
              {value?.supports_destroy && (
                <Confirm
                  subject={environment.id}
                  title={t("Delete environment target")}
                  description={t(
                    "Files and processes on the target will be lost. The next use automatically creates a fresh target from the frozen template revision; old files are not restored. Environment identity and history are retained.",
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
        <ErrorToast error={command.error} />
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
                    {can("environment.manage") && !renaming && (
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
                  <Fact label={t("Activity")}>
                    <StatePill state={value.retention_condition} />
                  </Fact>
                  <Fact label={t("Activity since")}>
                    <Timestamp value={value.condition_since} />
                  </Fact>
                  <Fact label={t("Generation")}>{value.generation}</Fact>
                  <Fact label={t("Updated")}>
                    <Timestamp value={value.updated_at} />
                  </Fact>
                </dl>
                {renaming && can("environment.manage") && (
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
                    {t(value.ownership === "managed" ? "Managed" : "External")}
                  </Fact>
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
                  {value.template_revision_id && (
                    <Fact label={t("Template revision")}>
                      <CopyableId value={value.template_revision_id} />
                    </Fact>
                  )}
                </dl>
              </section>
              <RetentionDetails environment={value} />
            </>
          )
        )}
        {command.data && (
          <section className={styles.factGroup} role="status">
            <h3>{t("Lifecycle command")}</h3>
            <dl className={styles.facts}>
              <Fact label={t("Status")}>
                <StatePill state={command.data.status} />
              </Fact>
              <Fact label={t("Command")}>
                <CopyableId value={command.data.id} />
              </Fact>
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

function RetentionDetails({
  environment,
}: {
  environment: Schema["EnvironmentDetail"];
}) {
  const { t, i18n } = useTranslation();
  const policy = environment.retention;
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
                [t("Stop after idle"), policy.idle.stop_after],
                [t("Delete after idle"), policy.idle.delete_after],
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
                "Frozen at allocation. Later template changes do not affect this environment.",
              )}
            </p>
            <p className={styles.panelNote}>
              {t(
                "Idle time starts when no runs actively use this environment. Stopping does not reset the deletion timer.",
              )}
            </p>
            <p className={styles.panelNote}>
              {t(
                "After target deletion, the next use creates a fresh target from the frozen template revision. Old files are not restored.",
              )}
            </p>
          </div>
        </>
      )}
    </section>
  );
}

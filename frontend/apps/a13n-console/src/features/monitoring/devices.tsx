import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, FormField, Input, ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data, ifMatch, rowTag, type Schema } from "../../shared/api";
import {
  CollectionFooter,
  Empty,
  ResourceIdentity,
  ResourceTable,
  Toolbar,
} from "../../shared/collection";
import { Confirm } from "../../shared/dialogs";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { FormActions, SecretReveal } from "../../shared/forms";
import { Page, PageActions } from "../../shared/page";
import { monitoringApi } from "./api";
import styles from "./monitoring.module.css";

export function DevicesPage() {
  const client = useClient(),
    { workspace, can, basePath } = useWorkspace(),
    { t } = useTranslation();
  const [search, setSearch] = useState(""),
    [editing, setEditing] = useState<Schema["Device"] | "new">();
  const devices = useQuery({
    queryKey: ["monitoring-devices", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client
          .workspace(workspace.id)
          .GET("/api/v1/devices", {
            params: { query: { cursor, limit: 100 } },
            signal,
          })
          .then(data),
      ),
  });
  const [status, setStatus] = useState("");
  const items = (devices.data ?? []).filter(
    (item) =>
      [item.name, item.id, item.device_id]
        .join(" ")
        .toLowerCase()
        .includes(search.toLowerCase()) &&
      (!status || item.enabled === (status === "enabled")),
  );
  return (
    <Page
      title={t("Monitoring devices")}
      back={`${basePath}/monitoring`}
      toolbar={
        <Toolbar
          search={search}
          onSearchChange={setSearch}
          searchLabel={t("Search devices")}
          filters={
            <>
              <ChoiceField
                label={t("Status")}
                variant="filter"
                value={status}
                options={[
                  { value: "", label: t("All") },
                  { value: "enabled", label: t("Enabled") },
                  { value: "disabled", label: t("Disabled") },
                ]}
                onValueChange={setStatus}
              />
              {(search || status) && (
                <Button
                  variant="ghost"
                  onClick={() => {
                    setSearch("");
                    setStatus("");
                  }}
                >
                  {t("Clear filters")}
                </Button>
              )}
            </>
          }
        />
      }
    >
      <PageActions>
        {can("admin") && (
          <Button onClick={() => setEditing("new")}>
            {t("Register device")}
          </Button>
        )}
      </PageActions>
      <ErrorNotice error={devices.error} />
      {devices.isPending ? (
        <Loading variant="table" columns={3} />
      ) : items.length ? (
        <>
          <ResourceTable
            caption={t("Monitoring devices")}
            items={items}
            onRowActivate={(item) => setEditing(item)}
            columns={[
              {
                label: t("Name"),
                tone: "primary",
                render: (item) => (
                  <ResourceIdentity
                    name={item.name}
                    resourceId={item.id}
                    description={item.device_id}
                  />
                ),
              },
              {
                label: t("Status"),
                render: (item) => t(item.enabled ? "Enabled" : "Disabled"),
              },
              {
                label: t("Metrics"),
                render: (item) => (
                  <Button
                    size="sm"
                    variant="ghost"
                    render={
                      <Link
                        to={`${basePath}/monitoring?target=device:${item.id}`}
                      />
                    }
                  >
                    {t("Open dashboard")}
                  </Button>
                ),
              },
            ]}
          />
          <CollectionFooter count={items.length} />
        </>
      ) : (
        <Empty
          title={t(search || status ? "No matching targets" : "No devices")}
          description={
            search || status
              ? t("Clear filters")
              : t(
                  "Register a device to receive metrics through its reverse carrier.",
                )
          }
        />
      )}
      {editing && (
        <DeviceEditor
          key={editing === "new" ? "new" : editing.id}
          device={editing === "new" ? undefined : editing}
          onClose={() => setEditing(undefined)}
        />
      )}
    </Page>
  );
}
function DeviceEditor({
  device,
  onClose,
}: {
  device?: Schema["Device"];
  onClose: () => void;
}) {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    cache = useQueryClient(),
    { t } = useTranslation(),
    http = client.workspace(workspace.id);
  const [basis, setBasis] = useState(device),
    [name, setName] = useState(device?.name ?? ""),
    [identity, setIdentity] = useState(device?.device_id ?? ""),
    [source, setSource] = useState(device?.metrics_data_source_id ?? ""),
    [enabled, setEnabled] = useState(device?.enabled ?? true),
    [token, setToken] = useState<string>();
  const sources = useQuery(monitoringApi(client, workspace.id).sources()),
    readonly = !can("admin");
  const invalidate = () =>
    cache.invalidateQueries({ queryKey: ["monitoring-devices", workspace.id] });
  const save = useMutation({
    mutationFn: async () => {
      if (basis) {
        await http
          .PATCH("/api/v1/devices/{device_id}", {
            params: { path: { device_id: basis.id } },
            headers: ifMatch(rowTag(basis)),
            body: { name, enabled, metrics_data_source_id: source || null },
          })
          .then(data);
        await invalidate();
        onClose();
      } else {
        const result = await http
          .POST("/api/v1/devices", {
            body: {
              name,
              device_id: identity,
              metrics_data_source_id: source || null,
            },
          })
          .then(data);
        setBasis(result.device);
        setToken(result.token);
        await invalidate();
      }
    },
  });
  return (
    <ModalFrame
      open
      onOpenChange={(value) => {
        if (!value && !save.isPending) onClose();
      }}
      title={device?.name ?? t("Register device")}
      closeLabel={t("Close")}
      size="lg"
    >
      {token ? (
        <div className={styles.fields}>
          <SecretReveal
            value={token}
            label={t("Device attachment token")}
            wrap
          />
          <p>
            {t(
              "Connect the Envd reverse carrier with this Bearer token and the eip.v1 subprotocol. Configure the sampler before connecting.",
            )}
          </p>
          <code>{`${location.origin.replace(/^http/, "ws")}/api/v1/devices/${basis!.id}/connect`}</code>
          <Button onClick={onClose}>{t("Done")}</Button>
        </div>
      ) : (
        <>
          <form
            className={styles.fields}
            onSubmit={(event) => {
              event.preventDefault();
              save.mutate();
            }}
          >
            <fieldset disabled={save.isPending} className={styles.fields}>
              <FormField label={t("Name")}>
                <Input
                  readOnly={readonly}
                  required
                  maxLength={128}
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </FormField>
              <FormField
                label={t("Native device identity")}
                description={t(
                  "Must match the Device identity reported by Envd.",
                )}
              >
                <Input
                  readOnly={!!basis || readonly}
                  required
                  value={identity}
                  onChange={(e) => setIdentity(e.target.value)}
                />
              </FormField>
              <ChoiceField
                label={t("Data source")}
                readOnly={readonly}
                value={source}
                options={[
                  { value: "", label: t("No monitoring") },
                  ...(source &&
                  !sources.data?.some((item) => item.id === source)
                    ? [
                        {
                          value: source,
                          label: `${t("Unavailable data source")} · ${source}`,
                          disabled: true,
                        },
                      ]
                    : []),
                  ...(sources.data ?? [])
                    .filter(
                      (item) =>
                        item.config.collection !== "pull" || item.id === source,
                    )
                    .map((item) => ({
                      value: item.id,
                      label: item.name,
                      disabled:
                        !item.enabled || item.config.collection === "pull",
                    })),
                ]}
                onValueChange={setSource}
              />
              {basis && (
                <ChoiceField
                  label={t("Status")}
                  readOnly={readonly}
                  value={enabled ? "yes" : "no"}
                  options={[
                    { value: "yes", label: t("Enabled") },
                    { value: "no", label: t("Disabled") },
                  ]}
                  onValueChange={(value) => setEnabled(value === "yes")}
                />
              )}
              <ErrorNotice error={save.error ?? sources.error} />
              <FormActions
                pending={save.isPending}
                label={t(basis ? "Save device" : "Register device")}
                dismiss={readonly}
                onCancel={onClose}
              />
            </fieldset>
          </form>
          {basis && !readonly && !save.isPending && (
            <div className={styles.toolbar}>
              <Confirm
                title={t("Rotate attachment token")}
                subject={basis.name}
                description={t(
                  "Disconnect the current carrier and invalidate its old token.",
                )}
                trigger={t("Rotate attachment token")}
                action={async () => {
                  const result = await http
                    .POST("/api/v1/devices/{device_id}/rotate-credential", {
                      params: { path: { device_id: basis.id } },
                      headers: ifMatch(rowTag(basis)),
                    })
                    .then(data);
                  setBasis(result.device);
                  setToken(result.token);
                }}
                onSuccess={() => void invalidate()}
              />
              <Confirm
                title={t("Delete device")}
                subject={basis.name}
                description={t(
                  "Disconnect this device and remove its registration.",
                )}
                trigger={t("Delete device")}
                triggerVariant="ghost"
                danger
                action={() =>
                  http.DELETE("/api/v1/devices/{device_id}", {
                    params: { path: { device_id: basis.id } },
                    headers: ifMatch(rowTag(basis)),
                  })
                }
                onSuccess={() => {
                  void invalidate();
                  onClose();
                }}
              />
            </div>
          )}
        </>
      )}
    </ModalFrame>
  );
}

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Dialog, Input, SelectField } from "a13n-ui";
import { Plus, ArrowLeft } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { Empty, ErrorNotice, Loading, StateBadge } from "../../shared/feedback";
import { Confirm, FormActions } from "../../shared/form";
import { Pagination, Table, useCursor } from "../../shared/collection";
import { ApiKeys } from "./keys";
import styles from "../../shared/shared.module.css";

export function ServiceAccounts() {
  const client = useClient(),
    { t } = useTranslation(),
    { workspace } = useWorkspace(),
    cache = useQueryClient(),
    page = useCursor();
  const [selected, setSelected] = useState<Schema["ServiceAccount"]>();
  const query = useQuery({
    queryKey: ["service-accounts", workspace.id, page.cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace_id}/service-accounts", {
          signal,
          params: {
            path: { workspace_id: workspace.id },
            query: { cursor: page.cursor, limit: 30 },
          },
        })
        .then(data),
  });
  if (selected)
    return (
      <div className={styles.stack}>
        <Button
          variant="ghost"
          icon={<ArrowLeft size={14} />}
          onClick={() => setSelected(undefined)}
        >
          {t("Service accounts")}
        </Button>
        <h2>{selected.name}</h2>
        <ApiKeys accountId={selected.id} />
      </div>
    );
  return (
    <div className={styles.stack}>
      <div className={styles.toolbar}>
        <p className={styles.muted}>
          {t("Workspace identities for automated applications.")}
        </p>
        <AccountEditor />
      </div>
      {query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorNotice error={query.error} />
      ) : query.data?.items.length ? (
        <>
          <Table
            items={query.data.items}
            columns={[
              {
                label: t("Name"),
                render: (item) => (
                  <Button variant="ghost" onClick={() => setSelected(item)}>
                    {item.name}
                  </Button>
                ),
              },
              {
                label: t("Role"),
                render: (item) =>
                  t(`role.${item.role}`, { defaultValue: item.role }),
              },
              {
                label: t("Status"),
                render: (item) => <StateBadge state={item.status} />,
              },
              {
                label: t("Actions"),
                render: (item) => (
                  <div className={styles.actions}>
                    <AccountEditor account={item} />
                    <Confirm
                      title={t("Delete service account")}
                      description={t(
                        "This revokes its keys and removes workspace access.",
                      )}
                      trigger={t("Delete")}
                      danger
                      action={async () => {
                        await client.http.DELETE(
                          "/api/v1/service-accounts/{account_id}",
                          {
                            params: { path: { account_id: item.id } },
                            body: { expected_version: item.version },
                          },
                        );
                        await cache.invalidateQueries({
                          queryKey: ["service-accounts", workspace.id],
                        });
                      }}
                    />
                  </div>
                ),
              },
            ]}
          />
          <Pagination page={page} next={query.data.next_cursor} />
        </>
      ) : (
        <Empty
          title={t("No service accounts")}
          description={t("Create a dedicated identity for each application.")}
        />
      )}
    </div>
  );
}
function AccountEditor({ account }: { account?: Schema["ServiceAccount"] }) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient();
  const [basis, setBasis] = useState(account),
    [open, setOpen] = useState(false),
    [name, setName] = useState(account?.name ?? ""),
    [role, setRole] = useState<"viewer" | "runner" | "builder">(
      account?.role === "runner" || account?.role === "builder"
        ? account.role
        : "viewer",
    ),
    [status, setStatus] = useState<"active" | "disabled">(
      account?.status === "disabled" ? "disabled" : "active",
    );
  function load(value: Schema["ServiceAccount"] | undefined) {
    setBasis(value);
    setName(value?.name ?? "");
    setRole(
      value?.role === "runner" || value?.role === "builder"
        ? value.role
        : "viewer",
    );
    setStatus(value?.status === "disabled" ? "disabled" : "active");
  }
  const reload = useMutation({
    mutationFn: () =>
      client.http
        .GET("/api/v1/service-accounts/{account_id}", {
          params: { path: { account_id: account!.id } },
        })
        .then(data),
    onSuccess: (value) => {
      load(value);
      mutation.reset();
    },
  });
  const mutation = useMutation({
    mutationFn: () =>
      basis
        ? client.http.PATCH("/api/v1/service-accounts/{account_id}", {
            params: { path: { account_id: basis.id } },
            body: { expected_version: basis.version, name, role, status },
          })
        : client.http.POST(
            "/api/v1/workspaces/{workspace_id}/service-accounts",
            {
              params: { path: { workspace_id: workspace.id } },
              body: { name, role },
            },
          ),
    onSuccess: () => {
      void cache.invalidateQueries({
        queryKey: ["service-accounts", workspace.id],
      });
      setOpen(false);
    },
  });
  return (
    <Dialog
      title={t(account ? "Edit service account" : "Create service account")}
      description={t("Select the minimum role this application needs.")}
      closeLabel={t("Close")}
      open={open}
      onOpenChange={(value) => {
        if (!mutation.isPending) {
          if (value) load(account);
          setOpen(value);
          mutation.reset();
        }
      }}
      trigger={
        <Button
          size="sm"
          variant={account ? "secondary" : "primary"}
          icon={!account && <Plus size={14} />}
        >
          {t(account ? "Edit" : "Create account")}
        </Button>
      }
    >
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          mutation.mutate();
        }}
      >
        <Input
          label={t("Name")}
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
        />
        <SelectField
          placeholder={t("Select…")}
          label={t("Role")}
          value={role}
          onValueChange={(value) => {
            if (value === "viewer" || value === "runner" || value === "builder")
              setRole(value);
          }}
          options={["viewer", "runner", "builder"].map((value) => ({
            value,
            label: t(`role.${value}`, { defaultValue: value }),
          }))}
        />
        {account && (
          <SelectField
            placeholder={t("Select…")}
            label={t("Status")}
            value={status}
            onValueChange={(value) => {
              if (value === "active" || value === "disabled") setStatus(value);
            }}
            options={[
              { value: "active", label: t("Active") },
              { value: "disabled", label: t("Disabled") },
            ]}
          />
        )}
        <ErrorNotice
          error={mutation.error ?? reload.error}
          retry={account ? () => reload.mutate() : undefined}
        />
        <FormActions pending={mutation.isPending} />
      </form>
    </Dialog>
  );
}

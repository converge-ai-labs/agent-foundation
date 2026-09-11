import { ResourceEditorButton } from "../../shared/resource-editor-button";
import { Button, ChoiceField, FormField, Input, ModalFrame } from "a13n-ui";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { PageActions } from "../../shared/page-actions";

import { ArrowLeftIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import { Empty, ErrorNotice, Loading, StateBadge } from "../../shared/feedback";
import { Confirm, FormActions } from "../../shared/form";
import styles from "../../shared/shared.module.css";
import { ApiKeys } from "./keys";

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
        .GET("/api/v1/workspaces/{workspace}/service-accounts", {
          signal,
          params: {
            path: { workspace: workspace.id },
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
          onClick={() => setSelected(undefined)}
          type="button"
        >
          {<ArrowLeftIcon size={14} />}
          {t("Service accounts")}
        </Button>
        <h2>{selected.name}</h2>
        <ApiKeys accountId={selected.id} />
      </div>
    );
  return (
    <div className={styles.stack}>
      <PageActions>
        <AccountEditor />
      </PageActions>
      {query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorNotice error={query.error} />
      ) : query.data?.items.length ? (
        <>
          <ResourceTable
            items={query.data.items}
            columns={[
              {
                label: t("Name"),
                tone: "primary",
                render: (item) => (
                  <Button
                    variant="ghost"
                    onClick={() => setSelected(item)}
                    type="button"
                  >
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
                align: "right",
                render: (item) => (
                  <div className={styles.actions}>
                    <AccountEditor account={item} />
                    <Confirm
                      triggerVariant="ghost"
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
        : client.http.POST("/api/v1/workspaces/{workspace}/service-accounts", {
            params: { path: { workspace: workspace.id } },
            body: { name, role },
          }),
    onSuccess: () => {
      void cache.invalidateQueries({
        queryKey: ["service-accounts", workspace.id],
      });
      setOpen(false);
    },
  });
  return (
    <ModalFrame
      onOpenChange={(value) => {
        if (!mutation.isPending) {
          if (value) load(account);
          setOpen(value);
          mutation.reset();
        }
      }}
      trigger={
        <ResourceEditorButton
          editing={!!account}
          createLabel="Create account"
          editLabel="Edit"
        />
      }
      size={"md"}
      title={t(account ? "Edit service account" : "Create service account")}
      description={t("Select the minimum role this application needs.")}
      closeLabel={t("Close")}
      open={open}
    >
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          mutation.mutate();
        }}
      >
        <FormField className="min-w-0 w-full" label={t("Name")}>
          <Input
            required={true}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </FormField>
        <ChoiceField
          placeholder={t("Select…")}
          value={role}
          className="min-w-0"
          onValueChange={(value) => {
            if (value === "viewer" || value === "runner" || value === "builder")
              setRole(value);
          }}
          label={t("Role")}
          options={["viewer", "runner", "builder"].map((value) => ({
            value,
            label: t(`role.${value}`, { defaultValue: value }),
          }))}
        />
        {account && (
          <ChoiceField
            placeholder={t("Select…")}
            value={status}
            className="min-w-0"
            onValueChange={(value) => {
              if (value === "active" || value === "disabled") setStatus(value);
            }}
            label={t("Status")}
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
    </ModalFrame>
  );
}

import { Button, ChoiceField, FormField, ModalFrame } from "a13n-ui";

import { SearchPicker } from "a13n-ui";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { PageActions } from "../../shared/page-actions";

import { ApiError } from "@converge.ai/a13n";
import { Plus } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { UserAvatar as Avatar } from "../../layout/avatar";
import { useAccess } from "../../layout/workspace";
import { allPages, data, representation, type Schema } from "../../shared/api";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import { Empty, ErrorNotice, Loading } from "../../shared/feedback";
import { Confirm, FormActions } from "../../shared/form";
import styles from "../../shared/shared.module.css";

export type MembershipScope = {
  kind: "workspace" | "organization";
  id: string;
};
type Role = Schema["ChangeRoleRequest"]["role"];
const roles: Role[] = ["member", "viewer", "runner", "builder", "admin"];
export function roleOptions(kind: MembershipScope["kind"]) {
  return roles.filter((role) =>
    kind === "organization"
      ? ["member", "admin"].includes(role)
      : role !== "member",
  );
}

export function Members({ scope }: { scope: MembershipScope }) {
  const { t } = useTranslation(),
    client = useClient(),
    { organization, organizationAdmin } = useAccess(),
    page = useCursor();
  const members = useQuery({
    queryKey: ["members", scope.kind, scope.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        scope.kind === "organization"
          ? client.http
              .GET("/api/v1/organizations/{organization}/users", {
                params: {
                  path: { organization: scope.id },
                  query: { cursor, limit: 100 },
                },
                signal,
              })
              .then(data)
          : client.http
              .GET("/api/v1/workspaces/{workspace}/members", {
                params: {
                  path: { workspace: scope.id },
                  query: { cursor, limit: 100 },
                },
                signal,
              })
              .then(data),
      ),
  });
  const bindings = useQuery({
    queryKey: ["bindings", scope.kind, scope.id, page.cursor],
    queryFn: ({ signal }) =>
      scope.kind === "organization"
        ? client.http
            .GET("/api/v1/organizations/{organization}/role-bindings", {
              params: {
                path: { organization: scope.id },
                query: { cursor: page.cursor, limit: 30 },
              },
              signal,
            })
            .then(data)
        : client.http
            .GET("/api/v1/workspaces/{workspace}/role-bindings", {
              params: {
                path: { workspace: scope.id },
                query: { cursor: page.cursor, limit: 30 },
              },
              signal,
            })
            .then(data),
  });
  const readVersion = async (item: Schema["RoleBinding"]) => {
    const latest = representation(
      await client.http.GET("/api/v1/role-bindings/{binding_id}", {
        params: { path: { binding_id: item.id } },
      }),
    );
    if (latest.value.updated_at !== item.updated_at || !latest.etag)
      throw new ApiError(
        412,
        "precondition_failed",
        t("The member's role changed. Reload before continuing."),
        {},
        null,
      );
    return latest.etag;
  };
  return (
    <div className={styles.stack}>
      <PageActions>
        {scope.kind === "workspace" && organizationAdmin && (
          <AddMember scope={scope} organizationId={organization.id} />
        )}
      </PageActions>
      {members.isPending || bindings.isPending ? (
        <Loading />
      ) : members.error || bindings.error ? (
        <ErrorNotice error={members.error ?? bindings.error} />
      ) : bindings.data?.items.length ? (
        <>
          <ResourceTable
            items={bindings.data.items}
            columns={[
              {
                label: t("Member"),
                render: (item) => {
                  const user = members.data?.find(
                    (user) => user.id === item.principal_id,
                  );
                  return (
                    <div className={styles.actions}>
                      <Avatar
                        name={user?.name ?? item.principal_type}
                        url={user?.image_url}
                      />
                      <span>
                        {user?.name ?? item.principal_id}
                        <small>{user?.email ?? item.principal_type}</small>
                      </span>
                    </div>
                  );
                },
              },
              {
                label: t("Role"),
                render: (item) =>
                  t(`role.${item.role_key}`, { defaultValue: item.role_key }),
              },
              {
                label: t("Actions"),
                align: "right",
                render: (item) => (
                  <div className={styles.actions}>
                    <ChangeRole
                      item={item}
                      scope={scope}
                      readVersion={readVersion}
                    />
                    <Confirm
                      title={t("Remove member")}
                      description={t(
                        "This removes the selected role. Other explicit grants may still allow access.",
                      )}
                      trigger={t("Remove")}
                      danger
                      action={async () => {
                        const etag = await readVersion(item);
                        await client.http.DELETE(
                          "/api/v1/role-bindings/{binding_id}",
                          {
                            params: {
                              path: { binding_id: item.id },
                              header: { "If-Match": etag },
                            },
                          },
                        );
                      }}
                    />
                  </div>
                ),
              },
            ]}
          />
          <Pagination page={page} next={bindings.data.next_cursor} />
        </>
      ) : (
        <Empty
          title={t("No members")}
          description={t("Invite a teammate to start collaborating.")}
        />
      )}
    </div>
  );
}
function ChangeRole({
  item,
  scope,
  readVersion,
}: {
  item: Schema["RoleBinding"];
  scope: MembershipScope;
  readVersion: (basis: Schema["RoleBinding"]) => Promise<string>;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient();
  const [basis, setBasis] = useState(item),
    [role, setRole] = useState<Role>(
      roles.find((role) => role === item.role_key) ?? "viewer",
    ),
    [open, setOpen] = useState(false);
  const change = useMutation({
    mutationFn: async () => {
      const etag = await readVersion(basis);
      await client.http.PATCH("/api/v1/role-bindings/{binding_id}", {
        params: { path: { binding_id: item.id }, header: { "If-Match": etag } },
        body: { role },
      });
    },
    onSuccess: () => {
      void cache.invalidateQueries();
      setOpen(false);
    },
  });
  const reload = useMutation({
    mutationFn: () =>
      client.http
        .GET("/api/v1/role-bindings/{binding_id}", {
          params: { path: { binding_id: item.id } },
        })
        .then(data),
    onSuccess: (latest) => {
      setBasis(latest);
      setRole(roles.find((role) => role === latest.role_key) ?? "viewer");
      change.reset();
    },
  });
  return (
    <ModalFrame
      onOpenChange={(value) => {
        if (!change.isPending) {
          if (value) {
            setBasis(item);
            setRole(roles.find((role) => role === item.role_key) ?? "viewer");
          }
          setOpen(value);
          change.reset();
        }
      }}
      trigger={
        <Button size="sm" variant="outline" type="button">
          {t("Change role")}
        </Button>
      }
      size={"md"}
      title={t("Change role")}
      description={t("Role changes apply immediately.")}
      closeLabel={t("Close")}
      open={open}
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          change.mutate();
        }}
      >
        <ChoiceField
          placeholder={t("Select role")}
          value={role}
          className="min-w-0"
          onValueChange={(value) => {
            const role = roles.find((role) => role === value);
            if (role) setRole(role);
          }}
          label={t("Role")}
          options={roleOptions(scope.kind).map((value) => ({
            value,
            label: t(`role.${value}`, { defaultValue: value }),
          }))}
        />
        <ErrorNotice
          error={reload.error ?? change.error}
          retry={() => reload.mutate()}
        />
        <FormActions pending={change.isPending} />
      </form>
    </ModalFrame>
  );
}
function AddMember({
  scope,
  organizationId,
}: {
  scope: MembershipScope;
  organizationId: string;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient();
  const [open, setOpen] = useState(false),
    [userId, setUserId] = useState(""),
    [role, setRole] = useState<Role>("viewer");
  const users = useQuery({
    queryKey: ["organization-users", organizationId],
    enabled: open,
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/organizations/{organization}/users", {
            params: {
              path: { organization: organizationId },
              query: { cursor, limit: 100 },
            },
            signal,
          })
          .then(data),
      ),
  });
  const add = useMutation({
    mutationFn: () =>
      client.http.POST("/api/v1/workspaces/{workspace}/role-bindings", {
        params: { path: { workspace: scope.id } },
        body: { principal_id: userId, role },
      }),
    onSuccess: () => {
      void cache.invalidateQueries();
      setOpen(false);
    },
  });
  return (
    <ModalFrame
      onOpenChange={setOpen}
      trigger={
        <Button variant="outline" type="button">
          {<Plus size={14} />}
          {t("Add member")}
        </Button>
      }
      size={"md"}
      title={t("Add existing member")}
      description={t(
        "Choose someone who already belongs to your organization.",
      )}
      closeLabel={t("Close")}
      open={open}
    >
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          if (userId) add.mutate();
        }}
      >
        <FormField label={t("Member")}>
          <SearchPicker
            label={t("Member")}
            placeholder={t("Find a member…")}
            emptyMessage={t("No members found")}
            value={userId}
            groups={[
              {
                label: t("Organization members"),
                options:
                  users.data?.map((user) => ({
                    value: user.id,
                    label: user.name,
                    description: user.email,
                  })) ?? [],
              },
            ]}
            onValueChange={setUserId}
          />
        </FormField>
        <ChoiceField
          placeholder={t("Select role")}
          value={role}
          className="min-w-0"
          onValueChange={(value) => {
            const role = roles.find((role) => role === value);
            if (role) setRole(role);
          }}
          label={t("Role")}
          options={roleOptions(scope.kind).map((value) => ({
            value,
            label: t(`role.${value}`, { defaultValue: value }),
          }))}
        />
        <ErrorNotice error={users.error ?? add.error} />
        <FormActions pending={add.isPending} label={t("Add member")} />
      </form>
    </ModalFrame>
  );
}

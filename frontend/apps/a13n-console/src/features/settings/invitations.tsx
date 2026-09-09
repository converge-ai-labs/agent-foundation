import { PageActions } from "../../shared/page-actions";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Dialog, Input, SelectField } from "a13n-ui";
import { Plus } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, type Schema } from "../../shared/api";
import {
  Empty,
  ErrorNotice,
  Loading,
  StateBadge,
  Timestamp,
} from "../../shared/feedback";
import { Confirm, FormActions } from "../../shared/form";
import { Pagination, Table, useCursor } from "../../shared/collection";
import { roleOptions, type MembershipScope } from "./members";
import { SecretReveal } from "./keys";
import styles from "../../shared/shared.module.css";

export function Invitations({ scope }: { scope: MembershipScope }) {
  const client = useClient(),
    { t } = useTranslation(),
    page = useCursor();
  const query = useQuery({
    queryKey: ["invitations", scope.kind, scope.id, page.cursor],
    queryFn: ({ signal }) =>
      scope.kind === "workspace"
        ? client.http
            .GET("/api/v1/workspaces/{workspace_id}/invitations", {
              signal,
              params: {
                path: { workspace_id: scope.id },
                query: { cursor: page.cursor, limit: 30 },
              },
            })
            .then(data)
        : client.http
            .GET("/api/v1/organizations/{organization_id}/invitations", {
              signal,
              params: {
                path: { organization_id: scope.id },
                query: { cursor: page.cursor, limit: 30 },
              },
            })
            .then(data),
  });
  return (
    <div className={styles.stack}>
      <PageActions>
        <InvitationEditor scope={scope} />
      </PageActions>
      {query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorNotice error={query.error} />
      ) : query.data?.items.length ? (
        <>
          <Table
            items={query.data.items}
            columns={[
              { label: t("Email"), render: (item) => item.email },
              {
                label: t("Role"),
                render: (item) =>
                  item.grants
                    .map((grant) =>
                      t(`role.${grant.role_key}`, {
                        defaultValue: grant.role_key,
                      }),
                    )
                    .join(", "),
              },
              {
                label: t("Expires"),
                render: (item) => <Timestamp value={item.expires_at} />,
              },
              {
                label: t("Status"),
                render: (item) => (
                  <StateBadge
                    state={
                      item.accepted_at
                        ? "accepted"
                        : item.revoked_at
                          ? "revoked"
                          : new Date(item.expires_at).getTime() < Date.now()
                            ? "expired"
                            : "pending"
                    }
                  />
                ),
              },
              {
                label: t("Actions"),
                align: "right",
                render: (item) =>
                  !item.accepted_at &&
                  !item.revoked_at && (
                    <div className={styles.actions}>
                      <InvitationEditor scope={scope} invitation={item} />
                      <Confirm
                        title={t("Revoke invitation")}
                        description={t(
                          "The invitation link will stop working.",
                        )}
                        trigger={t("Revoke")}
                        danger
                        action={() =>
                          client.http.POST(
                            "/api/v1/invitations/{invitation_id}/revoke",
                            {
                              params: { path: { invitation_id: item.id } },
                              body: { expected_version: item.version },
                            },
                          )
                        }
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
          title={t("No invitations")}
          description={t("Invite a teammate to start collaborating.")}
        />
      )}
    </div>
  );
}
function InvitationEditor({
  scope,
  invitation,
}: {
  scope: MembershipScope;
  invitation?: Schema["Invitation"];
}) {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient();
  const [open, setOpen] = useState(false),
    [email, setEmail] = useState(""),
    [role, setRole] = useState<Schema["ChangeRoleRequest"]["role"]>(
      scope.kind === "organization" ? "member" : "viewer",
    );
  const mutation = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      if (invitation)
        return client.http
          .POST("/api/v1/invitations/{invitation_id}/resend", {
            params: { path: { invitation_id: invitation.id } },
            body: { expected_version: invitation.version },
          })
          .then(data);
      if (scope.kind === "workspace") {
        if (role === "member") throw new Error("Invalid workspace role");
        return client.http
          .POST("/api/v1/workspaces/{workspace_id}/invitations", {
            params: { path: { workspace_id: scope.id } },
            body: { email, role },
          })
          .then(data);
      }
      return client.http
        .POST("/api/v1/organizations/{organization_id}/invitations", {
          params: { path: { organization_id: scope.id } },
          body: {
            email,
            grants: [
              {
                resource_type: "organization",
                resource_id: scope.id,
                role_key: role,
              },
            ],
          },
        })
        .then(data);
    },
    onSuccess: () => {
      void cache.invalidateQueries({
        queryKey: ["invitations", scope.kind, scope.id],
      });
    },
  });
  return (
    <Dialog
      title={t(invitation ? "Resend invitation" : "Invite member")}
      description={t(
        invitation
          ? "A new link replaces the previous invitation link."
          : "New members receive a single-use invitation link.",
      )}
      closeLabel={t("Close")}
      open={open}
      onOpenChange={(value) => {
        if (!mutation.isPending) {
          setOpen(value);
          mutation.reset();
        }
      }}
      trigger={
        <Button
          variant={invitation ? "secondary" : "primary"}
          size={invitation ? "sm" : "md"}
          icon={!invitation && <Plus size={14} />}
        >
          {t(invitation ? "Resend" : "Invite member")}
        </Button>
      }
    >
      {mutation.data ? (
        <div className={styles.stack}>
          <StateBadge state={mutation.data.delivery} />
          {mutation.data.invitation_url ? (
            <SecretReveal value={mutation.data.invitation_url} />
          ) : (
            <p>
              {t(
                mutation.data.delivery === "sent"
                  ? "Invitation sent"
                  : "Email delivery failed. Retry when delivery is available.",
              )}
            </p>
          )}
        </div>
      ) : (
        <form
          className={styles.form}
          onSubmit={(event) => {
            event.preventDefault();
            mutation.mutate();
          }}
        >
          {!invitation && (
            <>
              <Input
                label={t("Email address")}
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                required
              />
              <SelectField
                label={t("Role")}
                placeholder={t("Select role")}
                value={role}
                onValueChange={(value) => {
                  const role = roleOptions(scope.kind).find(
                    (role) => role === value,
                  );
                  if (role) setRole(role);
                }}
                options={roleOptions(scope.kind).map((value) => ({
                  value,
                  label: t(`role.${value}`, { defaultValue: value }),
                }))}
              />
            </>
          )}
          <ErrorNotice error={mutation.error} />
          <FormActions
            pending={mutation.isPending}
            label={t(invitation ? "Resend invitation" : "Send invitation")}
          />
        </form>
      )}
    </Dialog>
  );
}

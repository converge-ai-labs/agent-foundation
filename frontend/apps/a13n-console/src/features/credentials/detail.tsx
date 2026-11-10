import { KeyIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, SettingsRow, SettingsSection, useToast } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data } from "../../shared/api";
import {
  ErrorNotice,
  ErrorToast,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { IconTile } from "../../shared/identity";
import { DetailHeader, DetailPage, useTabParam } from "../../shared/page";
import shared from "../../shared/shared.module.css";
import { credentialKeys, credentialQueries, type Credential } from "./api";
import { useCredentialEditor } from "./editor";
import { CredentialSessions } from "./sessions";
import { RevokeCredential } from "./revoke";
import styles from "./credentials.module.css";

export function CredentialDetail() {
  const { credentialId = "" } = useParams();
  const client = useClient(),
    { workspace, basePath, can } = useWorkspace(),
    { t } = useTranslation();
  const [tab, setTab] = useTabParam(["sessions", "configuration"]);
  const query = useQuery({
    ...credentialQueries(client, workspace.id).item(credentialId),
    enabled: can("read"),
  });
  if (query.isPending) return <Loading variant="detail" />;
  if (!query.data)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  const credential = query.data;
  return (
    <DetailPage
      back={`${basePath}/credentials`}
      backLabel={t("Credentials")}
      header={
        <DetailHeader
          name={credential.name}
          description={credential.description}
          resourceKey={credential.id}
          avatar={
            <IconTile size={44}>
              <KeyIcon size={22} aria-hidden="true" />
            </IconTile>
          }
          status={<StatePill state={credential.state} />}
          actions={
            can("write") && <CredentialActions credential={credential} />
          }
        />
      }
      tabs={[
        { value: "sessions", label: t("Session application") },
        { value: "configuration", label: t("Configuration") },
      ]}
      tab={tab}
      onTabChange={setTab}
    >
      {query.error && (
        <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      )}
      {tab === "sessions" ? (
        <div className={shared.stack}>
          <CredentialSync credential={credential} />
          <CredentialSessions
            key={credential.id}
            target={{ credentialId: credential.id }}
          />
        </div>
      ) : (
        <CredentialConfiguration credential={credential} />
      )}
    </DetailPage>
  );
}

function CredentialActions({ credential }: { credential: Credential }) {
  const edit = useCredentialEditor({ operation: { kind: "edit", credential } });
  const rotate = useCredentialEditor({
    operation: { kind: "rotate", credential },
  });
  return (
    <>
      {edit.button}
      {credential.state === "active" && rotate.button}
      {edit.dialog}
      {rotate.dialog}
    </>
  );
}

function CredentialSync({ credential }: { credential: Credential }) {
  const toast = useToast();
  const client = useClient(),
    cache = useQueryClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation();
  const sync = useMutation({
    mutationFn: () =>
      client
        .workspace(workspace.id)
        .POST("/api/v1/egress-credentials/{credential_id}/sync", {
          params: { path: { credential_id: credential.id } },
        })
        .then(data),
    onSuccess: () => {
      toast.add({
        type: "success",
        title: t(
          "Synchronization requested. Check each Session for confirmation; idle Sessions were not connected.",
        ),
      });
      void cache.invalidateQueries({
        queryKey: credentialKeys(workspace.id).root,
      });
    },
  });
  return (
    <div className={shared.stack}>
      <SettingsSection variant="outlined">
        <SettingsRow
          label={t("Credential version {{version}}", {
            version: credential.credential_version,
          })}
          description={t(
            credential.state === "revoked"
              ? "Revoked in Service. Idle Sessions may retain the old token until next use or expiry. Revoke it upstream for immediate invalidation."
              : "Saved in Service. Active Sessions update asynchronously; idle Sessions update before their next command.",
          )}
        >
          {can("write") && (
            <Button
              type="button"
              variant="outline"
              size="sm"
              loading={sync.isPending}
              onClick={() => sync.mutate()}
            >
              {t("Sync active Sessions")}
            </Button>
          )}
        </SettingsRow>
      </SettingsSection>
      <ErrorToast error={sync.error} />
    </div>
  );
}

function CredentialConfiguration({ credential }: { credential: Credential }) {
  const { t } = useTranslation(),
    { can } = useWorkspace();
  return (
    <div className={shared.stack}>
      <SettingsSection variant="outlined" title={t("Credential configuration")}>
        <SettingsRow
          label={t("Allowed domains")}
          description={t(
            "Domains are fixed at creation. Create a new credential to change them.",
          )}
        >
          <span className={styles.domains}>
            {credential.allowed_hosts.join(", ")}
          </span>
        </SettingsRow>
        <SettingsRow
          label={t("Token")}
          description={t(
            "Stored encrypted and never shown again. It is not included in agent context.",
          )}
        >
          <span>
            {t(
              credential.state === "revoked" ? "Removed from Service" : "Saved",
            )}
          </span>
        </SettingsRow>
        <SettingsRow label={t("Credential version")}>
          <span>{credential.credential_version}</span>
        </SettingsRow>
        <SettingsRow label={t("Updated")}>
          <Timestamp value={credential.updated_at} />
        </SettingsRow>
        {credential.revoked_at && (
          <SettingsRow label={t("Revoked")}>
            <Timestamp value={credential.revoked_at} />
          </SettingsRow>
        )}
      </SettingsSection>
      {can("write") && credential.state === "active" && (
        <SettingsSection variant="outlined" title={t("Revoke credential")}>
          <SettingsRow
            label={t("Revoke credential")}
            description={t(
              "Prevents new use and removes the stored token. Active Sessions remove it asynchronously. This cannot be undone.",
            )}
          >
            <RevokeCredential credential={credential} />
          </SettingsRow>
        </SettingsSection>
      )}
    </div>
  );
}

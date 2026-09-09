import { SettingsLayout } from "./layout";
import { useTranslation } from "react-i18next";
import { useWorkspace, useAccess } from "../../layout/workspace";
import { Page, Empty } from "../../shared/feedback";
import { Profile } from "./profile";
import { Members } from "./members";
import { Invitations } from "./invitations";
import { ApiKeys } from "./keys";
import { ServiceAccounts } from "./accounts";
import { Audit } from "./audit";
import { Models } from "../models/page";
import { Environments } from "../environments/page";
import { ConnectorProviders } from "../connectors/providers";
import { Workspaces } from "./workspaces";
export function WorkspaceSettings() {
  const { t } = useTranslation(),
    { workspace, can } = useWorkspace();
  const scope = { kind: "workspace", id: workspace.id } as const;
  return (
    <SettingsLayout
      scope="workspace"
      name={workspace.name}
      items={[
        {
          value: "profile",
          label: t("General"),
          content: (
            <Profile target={scope} editable={can("role_binding.manage")} />
          ),
        },
        ...(can("role_binding.manage")
          ? [
              {
                value: "members",
                label: t("Members"),
                content: <Members scope={scope} />,
              },
            ]
          : []),
        ...(can("invitation.manage")
          ? [
              {
                value: "invitations",
                label: t("Invitations"),
                content: <Invitations scope={scope} />,
              },
            ]
          : []),
        {
          value: "personal-keys",
          label: t("My API keys"),
          content: <ApiKeys />,
        },
        ...(can("api_key.manage")
          ? [
              {
                value: "member-keys",
                label: t("Member keys"),
                content: <ApiKeys memberKeys />,
              },
            ]
          : []),
        ...(can("service_account.manage")
          ? [
              {
                value: "accounts",
                label: t("Service accounts"),
                content: <ServiceAccounts />,
              },
            ]
          : []),
        ...(can("security_audit.read")
          ? [
              {
                value: "audit",
                label: t("Audit"),
                content: <Audit scope={scope} />,
              },
            ]
          : []),
      ]}
    />
  );
}
export function OrganizationSettings() {
  const { t } = useTranslation(),
    { organization, organizationAdmin } = useAccess();
  const scope = { kind: "organization", id: organization.id } as const;
  if (!organizationAdmin)
    return (
      <Page title={t("Organization settings")}>
        <Empty
          title={t("Access unavailable")}
          description={t(
            "An organization administrator can manage these settings.",
          )}
        />
      </Page>
    );
  return (
    <SettingsLayout
      scope="organization"
      name={organization.name}
      items={[
        {
          value: "profile",
          label: t("General"),
          content: <Profile target={scope} />,
        },
        {
          value: "members",
          label: t("Members"),
          content: <Members scope={scope} />,
        },
        {
          value: "invitations",
          label: t("Invitations"),
          content: <Invitations scope={scope} />,
        },
        {
          value: "models",
          label: t("Shared models"),
          content: <Models scope={scope} />,
        },
        {
          value: "environments",
          label: t("Shared environments"),
          content: <Environments scope={scope} />,
        },
        {
          value: "connectors",
          label: t("Shared connector providers"),
          content: <ConnectorProviders scope={scope} />,
        },
        {
          value: "workspaces",
          label: t("Workspaces"),
          content: <Workspaces />,
        },
        {
          value: "audit",
          label: t("Audit"),
          content: <Audit scope={scope} />,
        },
      ]}
    />
  );
}

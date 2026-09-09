import { useTranslation } from "react-i18next";
import { useAccess, useWorkspace } from "../../layout/workspace";
import { Empty, Page } from "../../shared/feedback";
import { ConnectorProviders } from "../connectors/providers";
import { EnvironmentProviders } from "../environments/providers";
import { EnvironmentTemplates } from "../environments/templates";
import { Models } from "../models/page";
import { Providers } from "../models/providers";
import { ServiceAccounts } from "./accounts";
import { Audit } from "./audit";
import { Invitations } from "./invitations";
import { ApiKeys } from "./keys";
import { SettingsLayout } from "./layout";
import { Members } from "./members";
import { Profile } from "./profile";
import { Workspaces } from "./workspaces";
export function WorkspaceSettings() {
  const { workspace, can } = useWorkspace();
  const scope = { kind: "workspace", id: workspace.id } as const;
  return (
    <SettingsLayout
      scope="workspace"
      content={{
        profile: (
          <Profile target={scope} editable={can("role_binding.manage")} />
        ),
        members: <Members scope={scope} />,
        invitations: <Invitations scope={scope} />,
        "personal-keys": <ApiKeys />,
        "member-keys": <ApiKeys memberKeys />,
        accounts: <ServiceAccounts />,
        audit: <Audit scope={scope} />,
      }}
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
      content={{
        profile: <Profile target={scope} />,
        members: <Members scope={scope} />,
        invitations: <Invitations scope={scope} />,
        models: <Models scope={scope} />,
        "model-providers": <Providers scope={scope} />,
        environments: <EnvironmentTemplates scope={scope} />,
        "environment-providers": <EnvironmentProviders scope={scope} />,
        connectors: <ConnectorProviders scope={scope} />,
        workspaces: <Workspaces />,
        audit: <Audit scope={scope} />,
      }}
    />
  );
}

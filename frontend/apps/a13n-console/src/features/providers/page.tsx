import { ChoiceField, Tabs, TabsList, TabsTab, TabsPanel } from "a13n-ui";
import {
  CubeIcon,
  MagnifyingGlassIcon,
  MonitorIcon,
  PlugIcon,
} from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router";
import { useAccess } from "../../layout/workspace";
import { Empty, Page } from "../../shared/feedback";
import { Providers } from "../models/providers";
import { SearchProviders } from "../search/page";
import { EnvironmentProviders } from "../environments/providers";
import { ConnectorProviders } from "../connectors/providers";

const categories = [
  {
    value: "models",
    label: "Model",
    icon: CubeIcon,
    component: Providers,
    description: "Connect model services and manage their credentials.",
  },
  {
    value: "search",
    label: "Search",
    icon: MagnifyingGlassIcon,
    component: SearchProviders,
    description: "Connect search services for your agents' web tools.",
  },
  {
    value: "environments",
    label: "Environment",
    icon: MonitorIcon,
    component: EnvironmentProviders,
    description: "Configure where your agents' environments run.",
  },
  {
    value: "connectors",
    label: "Connector",
    icon: PlugIcon,
    component: ConnectorProviders,
    description: "Connect integration services for external accounts.",
  },
];
export function ProvidersPage() {
  const { t } = useTranslation();
  const { workspace, organization, organizationAdmin } = useAccess();
  const [params, setParams] = useSearchParams();
  const category =
    categories.find((item) => item.value === params.get("category")) ??
    categories[0];
  const kind =
    params.get("scope") === "organization" || !workspace
      ? "organization"
      : "workspace";
  const scope =
    kind === "workspace" && workspace
      ? { kind: "workspace" as const, id: workspace.id }
      : { kind: "organization" as const, id: organization.id };
  function update(key: string, value: string) {
    setParams((current) => {
      const next = new URLSearchParams(current);
      next.set(key, value);
      return next;
    });
  }
  return (
    <Page
      title={t("Providers")}
      description={t("Manage the services and credentials your agents use.")}
    >
      <div className="mb-6 max-w-sm">
        <ChoiceField
          label={t("Scope")}
          value={kind}
          onValueChange={(value) => update("scope", value)}
          options={[
            ...(workspace
              ? [
                  {
                    value: "workspace",
                    label: `${t("Workspace")} · ${workspace.name}`,
                  },
                ]
              : []),
            ...(organizationAdmin || kind === "organization"
              ? [
                  {
                    value: "organization",
                    label: `${t("Organization")} · ${organization.name}`,
                  },
                ]
              : []),
          ]}
        />
      </div>
      <Tabs
        value={category.value}
        onValueChange={(value) => update("category", String(value))}
      >
        <TabsList
          aria-label={t("Provider category")}
          className="flex h-auto flex-wrap justify-start"
        >
          {categories.map(({ value, label, icon: Icon }) => (
            <TabsTab key={value} value={value}>
              <Icon className="size-4" />
              {t(label)}
            </TabsTab>
          ))}
        </TabsList>
        <p className="my-4 text-sm text-muted-foreground">
          {t(category.description)}{" "}
          {t(
            kind === "organization"
              ? "Organization providers are shared across workspaces."
              : "Includes shared organization providers. New providers belong to this workspace.",
          )}
        </p>
        {categories.map(({ value, component: Component }) => (
          <TabsPanel key={value} value={value}>
            {kind === "organization" && !organizationAdmin ? (
              <Empty
                title={t("Access unavailable")}
                description={t(
                  "An organization administrator can manage these settings.",
                )}
              />
            ) : (
              <Component key={`${scope.kind}:${scope.id}`} scope={scope} />
            )}
          </TabsPanel>
        ))}
      </Tabs>
    </Page>
  );
}

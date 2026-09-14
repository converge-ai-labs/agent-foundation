import { Tabs, TabsList, TabsTab, TabsPanel } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router";
import { useAccess } from "../../layout/workspace";
import { providerCategories, providerCategory } from "./categories";
import { Empty } from "../../shared/feedback";
import { Providers } from "../models/providers";
import { WebProviders } from "../web/page";
import { EnvironmentProviders } from "../environments/providers";
import { ConnectorProviders } from "../connectors/providers";

const components = {
  models: Providers,
  search: WebProviders,
  environments: EnvironmentProviders,
  connectors: ConnectorProviders,
};
export function ProvidersPage({
  scope,
}: {
  scope: { kind: "workspace" | "organization"; id: string };
}) {
  const { t } = useTranslation();
  const { organizationAdmin } = useAccess();
  const [params, setParams] = useSearchParams();
  const category = providerCategory(params.get("category"));
  const kind = scope.kind;
  function update(key: string, value: string) {
    setParams((current) => {
      const next = new URLSearchParams(current);
      next.set(key, value);
      return next;
    });
  }
  return (
    <Tabs
      value={category.value}
      onValueChange={(value) => update("category", String(value))}
    >
      <TabsList
        aria-label={t("Provider category")}
        className="flex h-auto flex-wrap justify-start"
      >
        {providerCategories.map(({ value, label, icon: Icon }) => (
          <TabsTab key={value} value={value}>
            <Icon className="size-4" />
            {t(label)}
          </TabsTab>
        ))}
      </TabsList>
      {providerCategories.map(({ value }) => {
        const Component = components[value];
        return (
          <TabsPanel key={value} value={value} className="mt-4">
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
        );
      })}
    </Tabs>
  );
}

import { Tabs, TabsList, TabsPanel, TabsTab } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router";
import { ConnectorProviders } from "../connectors/providers";
import { EnvironmentProviders } from "../environments/providers";
import { MemoryProviders } from "../memories/providers";
import { Providers } from "../models/providers";
import { WebProviders } from "../web/page";
import { providerCategories, providerCategory } from "./categories";
import styles from "./providers.module.css";

const components = {
  models: Providers,
  web: WebProviders,
  environments: EnvironmentProviders,
  connectors: ConnectorProviders,
  memory: MemoryProviders,
};
export function ProvidersPage() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const category = providerCategory(params.get("category"));
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
      <TabsList variant="underline" aria-label={t("Provider category")}>
        {providerCategories.map(({ value, label, icon: Icon }) => (
          <TabsTab key={value} value={value}>
            <Icon size={14} />
            {t(label)}
          </TabsTab>
        ))}
      </TabsList>
      {providerCategories.map(({ value }) => {
        const Component = components[value];
        return (
          <TabsPanel key={value} value={value} className={styles.panel}>
            <Component />
          </TabsPanel>
        );
      })}
    </Tabs>
  );
}

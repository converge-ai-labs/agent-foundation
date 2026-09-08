import { Tabs } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../layout/workspace";
import { Page } from "../../shared/feedback";
import { EnvironmentProviders } from "./providers";
import { EnvironmentTemplates } from "./templates";
import { EnvironmentInstances } from "./instances";
import type { EnvironmentScope } from "./api";
export function Environments({ scope }: { scope: EnvironmentScope }) {
  const { t } = useTranslation();
  return (
    <Tabs
      label={t("Environments")}
      defaultValue="templates"
      items={[
        {
          value: "templates",
          label: t("Templates"),
          content: <EnvironmentTemplates scope={scope} />,
        },
        {
          value: "providers",
          label: t("Providers"),
          content: <EnvironmentProviders scope={scope} />,
        },
        ...(scope.kind === "workspace"
          ? [
              {
                value: "instances",
                label: t("Instances"),
                content: <EnvironmentInstances />,
              },
            ]
          : []),
      ]}
    />
  );
}
export function EnvironmentsPage() {
  const { workspace } = useWorkspace(),
    { t } = useTranslation();
  return (
    <Page
      title={t("Environments")}
      description={t("Providers, reusable recipes, and working environments.")}
    >
      <Environments scope={{ kind: "workspace", id: workspace.id }} />
    </Page>
  );
}

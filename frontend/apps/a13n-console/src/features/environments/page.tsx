import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../layout/workspace";
import { Page } from "../../shared/feedback";
import { EnvironmentProviders } from "./providers";
import { EnvironmentTemplates } from "./templates";
import { EnvironmentInstances } from "./instances";

export function EnvironmentsPage({
  section = "templates",
}: {
  section?: "templates" | "providers" | "instances";
}) {
  const { workspace } = useWorkspace(),
    { t } = useTranslation();
  const scope = { kind: "workspace", id: workspace.id } as const;
  const titles = {
    templates: "Environment templates",
    providers: "Environment providers",
    instances: "Environment instances",
  };
  const descriptions = {
    templates: "Reusable recipes for your agents' working environments.",
    providers: "Configure where your agents' environments run.",
    instances: "Inspect the environments your agents are using.",
  };
  return (
    <Page title={t(titles[section])} description={t(descriptions[section])}>
      {section === "providers" ? (
        <EnvironmentProviders scope={scope} />
      ) : section === "instances" ? (
        <EnvironmentInstances />
      ) : (
        <EnvironmentTemplates scope={scope} />
      )}
    </Page>
  );
}

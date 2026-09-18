import { useTranslation } from "react-i18next";
import { useWorkspace } from "../../layout/workspace";
import { Page } from "../../shared/page";
import { EnvironmentInstances } from "./instances";
import { EnvironmentTemplates } from "./templates";

/** Two collections under one route family: what agents can run on, and what runs. */
export function EnvironmentsPage({
  section = "templates",
}: {
  section?: "templates" | "instances";
}) {
  const { workspace } = useWorkspace(),
    { t } = useTranslation();
  const scope = { kind: "workspace", id: workspace.id } as const;
  const titles = {
    templates: "Environment templates",
    instances: "Environment instances",
  };
  const descriptions = {
    templates: "Reusable templates for your agents' working environments.",
    instances: "Inspect the environments your agents are using.",
  };
  return (
    <Page title={t(titles[section])} description={t(descriptions[section])}>
      {section === "instances" ? (
        <EnvironmentInstances />
      ) : (
        <EnvironmentTemplates scope={scope} />
      )}
    </Page>
  );
}

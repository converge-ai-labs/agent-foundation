import { EnvironmentInstances } from "./instances";
import { EnvironmentTemplates } from "./templates";

/** Two collections under one route family: what agents can run on, and what runs. */
export function EnvironmentsPage({
  section = "templates",
}: {
  section?: "templates" | "instances";
}) {
  return section === "instances" ? (
    <EnvironmentInstances />
  ) : (
    <EnvironmentTemplates />
  );
}

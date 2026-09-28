import { workspacePath } from "../../shared/paths";

export function providersPath(category: string, workspace: { id: string }) {
  return `${workspacePath(workspace)}/settings/providers?${new URLSearchParams({ category })}`;
}

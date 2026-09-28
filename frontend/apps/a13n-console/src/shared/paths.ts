export function workspacePath(workspace: { id: string }): string {
  return `/workspace/${workspace.id}`;
}

import type { Schema } from "../transport/client";

export const readOnly: Schema<"EnvironmentAction">[] = [
  "environment.file.stat",
  "environment.file.read_text",
  "environment.file.read_bytes",
  "environment.file.list",
  "environment.file.query",
  "environment.file.search_text",
  "environment.file.copy_source",
];

// Store an exact ceiling: omitted ceilings retain legacy file/execution access.
export const fullControl: Schema<"EnvironmentAction">[] = [
  ...readOnly,
  "environment.file.write_text",
  "environment.file.patch_text",
  "environment.file.mkdir",
  "environment.file.move",
  "environment.file.remove",
  "environment.file.write_bytes",
  "environment.file.copy_destination",
  "environment.shell.exec",
  "environment.process.list",
  "environment.process.start",
  "environment.process.inspect",
  "environment.process.read_output",
  "environment.process.write_stdin",
  "environment.process.close_stdin",
  "environment.process.signal",
  "environment.process.wait",
  "environment.process.kill",
  "environment.process.release",
  "environment.output.read",
  "environment.output.release",
  "environment.port.inspect",
  "environment.port.wait",
  "environment.state.export",
  "environment.state.restore",
  "environment.computer.describe",
  "environment.computer.observe",
  "environment.computer.click",
  "environment.computer.move",
  "environment.computer.drag",
  "environment.computer.scroll",
  "environment.computer.type_text",
  "environment.computer.press_keys",
];

export function permissionPreset(
  ceiling: Schema<"EnvironmentPermissionSet"> | undefined,
): "read_only" | "full" | undefined {
  if (!ceiling) return undefined;
  const operations = new Set(ceiling.operations ?? []);
  for (const [preset, actions] of [
    ["read_only", readOnly],
    ["full", fullControl],
  ] as const) {
    if (
      operations.size === actions.length &&
      actions.every((action) => operations.has(action))
    )
      return preset;
  }
  return undefined;
}

export function permissionLabel(
  ceiling: Schema<"EnvironmentPermissionSet"> | undefined,
) {
  switch (permissionPreset(ceiling)) {
    case "read_only":
      return "Read only";
    case "full":
      return "Full control";
    default:
      return ceiling
        ? `${ceiling.operations?.length ?? 0} allowed actions`
        : "Files and execution";
  }
}

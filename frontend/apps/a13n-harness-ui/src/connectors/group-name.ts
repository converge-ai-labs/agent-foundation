export const groupNamePattern = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;

/** Suggest a readable alias; saved aliases are independent of display names. */
export function defaultGroupName(
  name: string,
  kind: string,
  used: Iterable<string>,
): string {
  const normalize = (value: string) =>
    value
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");
  let base = normalize(name) || normalize(kind) || "connection";
  if (!/^[a-z]/.test(base)) base = `connection-${base}`;
  base = base.slice(0, 32).replace(/-+$/g, "");
  const reserved = new Set(used);
  let candidate = base;
  for (let number = 2; reserved.has(candidate); number++) {
    const suffix = `-${number}`;
    candidate = base.slice(0, 32 - suffix.length).replace(/-+$/g, "") + suffix;
  }
  return candidate;
}

export function groupNameError(
  value: string,
  used: Iterable<string>,
): string | undefined {
  if (value.length > 32 || !groupNamePattern.test(value))
    return "Use up to 32 lowercase letters, digits and single hyphens, starting with a letter.";
  if (new Set(used).has(value))
    return "Group name is already used by another connection.";
}

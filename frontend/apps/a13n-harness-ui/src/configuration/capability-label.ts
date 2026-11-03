/** Display catalog keys as readable names without changing resource identity. */
export function capabilityLabel(
  key: string,
  t?: (key: string) => string,
): string {
  if (t) {
    const builtins: Record<string, string> = {
      dynamic_environment: t("Dynamic environment"),
      compaction: t("Compaction"),
      file_context: t("File context"),
      handoff: t("Handoff"),
      runtime_context: t("Runtime context"),
      documents: t("Documents"),
      skills: t("Skills"),
      web: t("Web"),
      native_image_generation: t("Native image generation"),
      working_state: t("Working state"),
      user_interaction: t("User interaction"),
    };
    if (Object.hasOwn(builtins, key)) return builtins[key];
  }
  if (key === "codeact") return "CodeAct";
  const words = key
    .replace(/([a-z\d])([A-Z])/g, "$1 $2")
    .replace(/([A-Z])([A-Z][a-z])/g, "$1 $2")
    .replaceAll("_", " ")
    .split(" ")
    .map((word) => (/^[A-Z\d]+$/.test(word) ? word : word.toLowerCase()));
  const label = words.join(" ");
  return label.charAt(0).toUpperCase() + label.slice(1);
}

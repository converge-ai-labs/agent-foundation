/** Display catalog keys as readable names without changing resource identity. */
export function capabilityLabel(key: string): string {
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

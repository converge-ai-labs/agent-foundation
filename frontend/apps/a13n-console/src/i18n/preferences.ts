const key = "a13n-console-language";
export type DisplayLanguage = "en" | "zh-CN" | "hi";
export type LanguagePreference = "system" | DisplayLanguage;

export function readLanguagePreference(): LanguagePreference {
  try {
    const value = localStorage.getItem(key);
    return value === "en" || value === "zh-CN" || value === "hi"
      ? value
      : "system";
  } catch {
    return "system";
  }
}

export function resolveLanguage(
  preference: LanguagePreference,
): DisplayLanguage {
  if (preference !== "system") return preference;
  const languages =
    typeof navigator === "undefined"
      ? []
      : navigator.languages?.length
        ? navigator.languages
        : [navigator.language];
  for (const language of languages) {
    if (/^zh(?:-|$)/i.test(language)) return "zh-CN";
    if (/^hi(?:-|$)/i.test(language)) return "hi";
    if (/^en(?:-|$)/i.test(language)) return "en";
  }
  return "en";
}

export function readLanguage(): DisplayLanguage {
  return resolveLanguage(readLanguagePreference());
}

export function saveLanguage(preference: LanguagePreference): void {
  try {
    localStorage.setItem(key, preference);
  } catch {
    /* The selected language still applies until this page is reloaded. */
  }
}

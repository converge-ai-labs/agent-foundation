const key = "a13n-console-language";
export function readLanguage(): string {
  try {
    return localStorage.getItem(key) === "zh-CN" ? "zh-CN" : "en";
  } catch {
    return "en";
  }
}
export function saveLanguage(language: string): void {
  try {
    localStorage.setItem(key, language);
  } catch {
    /* Keep the in-memory preference when storage is unavailable. */
  }
}

export function readPreference(key: string, fallback: string) {
  try {
    return localStorage.getItem(`a13n-harness-ui.${key}`) ?? fallback;
  } catch {
    return fallback;
  }
}
export function writePreference(key: string, value: string) {
  try {
    localStorage.setItem(`a13n-harness-ui.${key}`, value);
  } catch {
    /* Browser preferences are optional. */
  }
}

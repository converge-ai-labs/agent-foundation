/**
 * The one duration format: milliseconds below a second, seconds with two
 * decimals below a minute, then minutes with zero-padded seconds.
 */
export function formatDuration(ms: number, language?: string) {
  if (ms < 1000)
    return `${ms.toLocaleString(language, { maximumFractionDigits: ms < 10 ? 1 : 0 })} ms`;
  // 59.995 s and above would round to "60.00 s".
  if (ms < 59_995)
    return `${(ms / 1000).toLocaleString(language, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} s`;
  const seconds = Math.round(ms / 1000);
  return `${Math.floor(seconds / 60).toLocaleString(language)} m ${String(seconds % 60).padStart(2, "0")} s`;
}

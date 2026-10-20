export function formatLocalDateTime(
  date: Date,
  precision: "minute" | "second" = "minute",
): string {
  if (!Number.isFinite(date.getTime())) return "";
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000)
    .toISOString()
    .slice(0, precision === "second" ? 19 : 16);
}

export function parseLocalDateTime(value: string): Date | undefined {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?$/.test(value)) return undefined;
  const date = new Date(value);
  // Reject invalid dates and local times skipped by a daylight-saving transition.
  return formatLocalDateTime(
    date,
    value.length === 19 ? "second" : "minute",
  ) === value
    ? date
    : undefined;
}

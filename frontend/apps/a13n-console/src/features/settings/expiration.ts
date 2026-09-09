export const expirationOptions = [
  { value: "1d", label: "1 day" },
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "3m", label: "3 months" },
  { value: "1y", label: "1 year" },
  { value: "never", label: "No expiration" },
] as const;
export type Expiration = (typeof expirationOptions)[number]["value"];

export function expirationTimestamp(
  value: Expiration,
  now = new Date(),
): string | null {
  if (value === "never") return null;
  const days = { "1d": 1, "7d": 7, "30d": 30 };
  if (value in days)
    return new Date(
      now.getTime() + days[value as keyof typeof days] * 86_400_000,
    ).toISOString();
  const expires = new Date(now);
  // Calendar durations clamp the original day to the target month's last day.
  expires.setUTCDate(1);
  expires.setUTCMonth(expires.getUTCMonth() + (value === "3m" ? 3 : 12));
  const lastDay = new Date(
    Date.UTC(expires.getUTCFullYear(), expires.getUTCMonth() + 1, 0),
  ).getUTCDate();
  expires.setUTCDate(Math.min(now.getUTCDate(), lastDay));
  return expires.toISOString();
}

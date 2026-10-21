import type { Schema } from "../../shared/api";

export function sessionTitle(session: Schema["SessionView"]) {
  return session.title || session.origin?.name || null;
}

/** Older Services/records can lack an origin; links are never executable schemes. */
export function originLink(origin: Schema["SessionOrigin"] | null | undefined) {
  if (!origin?.url) return undefined;
  try {
    const url = new URL(origin.url);
    return ["https:", "http:"].includes(url.protocol) &&
      !url.username &&
      !url.password
      ? url.href
      : undefined;
  } catch {
    return undefined;
  }
}

export function sessionPath(
  basePath: string,
  sessionId: string,
  query?: string | null,
) {
  const thread =
    query && /^thread_[0-9a-f]{20,32}$/.test(query) ? query : undefined;
  return `${basePath}/sessions/${sessionId}${thread ? `/threads/${encodeURIComponent(thread)}` : ""}`;
}

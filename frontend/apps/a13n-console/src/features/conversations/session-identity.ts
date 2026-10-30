import type { Schema } from "../../shared/api";

export function sessionTitle(session: Schema["SessionView"]) {
  return session.title || session.origin?.name || null;
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

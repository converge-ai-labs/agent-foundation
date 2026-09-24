import { ApiError } from "../service-client";

const MAX_KEY_LENGTH = 64;
const RANDOM_ATTEMPTS = 8;

function keyConflict(error: unknown) {
  return error instanceof ApiError && error.code === "already_exists";
}

/** A key spelled from a name: lowercase words joined by hyphens. */
export function readableKey(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+/, "")
    .slice(0, MAX_KEY_LENGTH)
    .replace(/-+$/, "");
}

/**
 * The Service requires a caller-chosen key for a new agent or workspace. Try
 * the readable name once, then short random suffixes on it (or on `fallback`
 * when the name has no key characters), retrying only key conflicts.
 */
export async function createWithKey<T>(
  name: string,
  fallback: string,
  create: (key: string) => Promise<T>,
): Promise<T> {
  const readable = readableKey(name);
  if (readable && readable !== "new")
    try {
      return await create(readable);
    } catch (error) {
      if (!keyConflict(error)) throw error;
    }
  const base = (readable || fallback)
    .slice(0, MAX_KEY_LENGTH - 5)
    .replace(/-+$/, "");
  for (let attempt = 1; ; attempt++) {
    const suffix = crypto.getRandomValues(new Uint16Array(1))[0]!;
    try {
      return await create(`${base}-${suffix.toString(16).padStart(4, "0")}`);
    } catch (error) {
      if (!keyConflict(error) || attempt === RANDOM_ATTEMPTS) throw error;
    }
  }
}

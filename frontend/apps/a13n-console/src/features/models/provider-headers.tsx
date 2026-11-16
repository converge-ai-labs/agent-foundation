import type { Schema } from "../../shared/api";
import { serializeHeaders, type HeaderDraft } from "../../shared/forms";
export {
  HeaderFields as ProviderHeaders,
  serializeHeaders,
  type HeaderDraft,
} from "../../shared/forms";

export function initialHeaders(provider?: Schema["Provider"]): HeaderDraft[] {
  return (provider?.header_names ?? []).map((name) => ({
    id: name,
    name,
    value: "",
    savedName: name,
  }));
}

/** A new provider's headers: it has none saved, so every row sets a value. */
export function newHeaders(
  rows: HeaderDraft[],
  t: (key: string) => string,
): Record<string, string> {
  const headers: Record<string, string> = {};
  for (const [name, value] of Object.entries(serializeHeaders(rows, [], t)))
    if (value !== null) headers[name] = value;
  return headers;
}

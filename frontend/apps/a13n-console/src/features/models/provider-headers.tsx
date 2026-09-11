import type { Schema } from "../../shared/api";
import type { HeaderDraft } from "../../shared/header-fields";
export {
  HeaderFields as ProviderHeaders,
  serializeHeaders,
  type HeaderDraft,
} from "../../shared/header-fields";

export function initialHeaders(
  provider?: Schema["ModelProvider"],
): HeaderDraft[] {
  return (provider?.header_names ?? []).map((name) => ({
    id: name,
    name,
    value: "",
    savedName: name,
  }));
}

import { data, ProtocolError } from "../../service-client";
import type { Schema } from "../../shared/api";

/** Exact display fields shared by full observations and lightweight timeline rows. */
export type Observation = Omit<Schema["SpanOverview"], "view">;

/** Keep overview responses out of consumers that require complete observations. */
export function overviewData(result: {
  data?: Schema["SpanPage"] | Schema["SpanReadPage"];
  response: Response;
}): { items: Schema["SpanOverview"][]; next_cursor: string | null } {
  const page = data(result);
  const items = page.items.map((item) => {
    if (!("view" in item) || item.view !== "overview")
      throw new ProtocolError(
        "Expected a trace overview response from the Service.",
      );
    return item;
  });
  return { items, next_cursor: page.next_cursor };
}

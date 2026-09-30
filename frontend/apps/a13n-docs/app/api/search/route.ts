import { createFromSource } from "fumadocs-core/search/server";
import { source } from "@/lib/source";

export const revalidate = false;

// Results keep relevance order, so the exported index needs no sort data.
export const { staticGET: GET } = createFromSource(source, {
  sort: { enabled: false },
});

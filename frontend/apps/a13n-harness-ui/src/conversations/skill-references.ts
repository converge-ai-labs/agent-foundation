import type {
  CompletionContext,
  CompletionResult,
} from "@codemirror/autocomplete";
import type { Schema } from "../transport/client";
import type { OrderedInputPart } from "./inline-attachments";

export type SkillCatalog = Schema<"SkillCatalogView">;
export type LoadSkills = () => Promise<SkillCatalog>;
export function skillReferences(
  parts: OrderedInputPart[],
  catalog?: SkillCatalog,
) {
  // Match ComposerInput.text and the TUI's exact whitespace-delimited syntax.
  const text = parts
    .filter((part): part is string => typeof part === "string")
    .join("");
  const names = new Set(
    [...text.matchAll(/(?:^|\s)\$([^\s]+)/g)].map((match) => match[1]),
  );
  const items = new Map(catalog?.items.map((item) => [item.name, item]));
  return [...names].flatMap((name) => {
    const item = items.get(name);
    return item && catalog
      ? [{ catalog_id: catalog.catalog_id, item_id: item.item_id, name }]
      : [];
  });
}
export function skillCompletion(load: LoadSkills) {
  return async (
    context: CompletionContext,
  ): Promise<CompletionResult | null> => {
    const token = context.matchBefore(/(?:^|\s)\$[^\s]*/);
    if (!token || context.view?.compositionStarted) return null;
    const from = token.from + token.text.indexOf("$");
    const prefix = context.state.sliceDoc(from + 1, context.pos);
    let catalog: SkillCatalog;
    try {
      catalog = await load();
    } catch {
      // The composer reports catalog failures; keep ordinary input editable.
      return null;
    }
    if (context.aborted) return null;
    return {
      from,
      filter: false,
      options: catalog.items
        .filter((item) => !/\s/.test(item.name) && item.name.startsWith(prefix))
        .sort((a, b) => a.name.localeCompare(b.name))
        .map((item) => ({
          label: `$${item.name}`,
          detail: item.description.replace(/\s+/g, " ").slice(0, 160),
          apply: `$${item.name} `,
          type: "text",
        })),
    };
  };
}
